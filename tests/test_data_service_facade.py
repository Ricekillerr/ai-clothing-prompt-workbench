import tempfile
import hashlib
import json
import zipfile
import threading
import time
import unittest
import subprocess
import imageio_ffmpeg
from pathlib import Path
from unittest.mock import patch
from PIL import Image

from storage import DataService, Storage, StorageError, migrate


class DataServiceFacadeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Storage(Path(self.tmp.name))
        (self.store.home / 'references').mkdir(exist_ok=True)
        self.service = DataService(self.store)

    def test_asset_paths_and_backup_restore(self):
        self.service.save_asset('sample.png', b'example')
        self.assertEqual(self.service.get_asset('sample.png').read_bytes(), b'example')
        self.assertEqual(self.service.resolve_asset('sample.png').read_bytes(), b'example')
        for name in ('../outside', '/absolute.png', r'C:\outside.png', 'C:relative.png', 'nested/file.png', ''):
            with self.subTest(name=name), self.assertRaises(StorageError): self.service.get_asset(name)
            with self.subTest(name=name), self.assertRaises(StorageError): self.service.save_asset(name, b'x')
            with self.subTest(name=name), self.assertRaises(StorageError): self.service.resolve_asset(name)
        self.store.save_data({'cases': [{'id': 'case1', 'body': '原文'}], 'modules': []})
        archive = self.service.create_backup('test')
        self.store.save_data({'cases': [], 'modules': []})
        self.service.restore_backup(archive)
        self.assertEqual(self.store.load_data()['cases'][0]['body'], '原文')
        self.assertEqual(self.service.get_asset('sample.png').read_bytes(), b'example')

    def test_full_backup_blocks_concurrent_asset_write(self):
        entered = threading.Event(); release = threading.Event(); done = threading.Event(); errors = []
        original = self.store._create_full_backup_locked
        def delayed(*args, **kwargs):
            entered.set()
            if not release.wait(5): raise RuntimeError('test timeout')
            return original(*args, **kwargs)
        def backup():
            try: self.service.create_backup('test')
            except Exception as exc: errors.append(exc)
        def save():
            try: self.service.save_asset('later.png', b'contents')
            except Exception as exc: errors.append(exc)
            finally: done.set()
        with patch.object(self.store, '_create_full_backup_locked', side_effect=delayed):
            first = threading.Thread(target=backup); first.start()
            self.assertTrue(entered.wait(5))
            second = threading.Thread(target=save); second.start()
            try:
                self.assertFalse(done.wait(0.2))
            finally:
                release.set(); first.join(10); second.join(10)
        self.assertFalse(first.is_alive()); self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(done.is_set())

    def test_verify_backup_is_independent_and_rejects_corruption(self):
        image = self.store.home / 'references' / 'valid.png'
        Image.new('RGB', (8, 8), 'blue').save(image)
        self.store.save_data({'cases': [{'id': 'case1', 'body': '原文', 'image': 'valid.png'}], 'modules': []})
        archive = self.service.create_backup('test')
        before = hashlib.sha256(self.store.db_path.read_bytes()).hexdigest()
        with patch.object(Storage, 'restore_full_backup', side_effect=AssertionError('restore invoked')), \
             patch.object(DataService, 'restore_backup', side_effect=AssertionError('restore invoked')):
            result = self.service.verify_backup(archive)
        self.assertTrue(result['ok'])
        self.assertEqual(result['cases'], 1)
        self.assertEqual(hashlib.sha256(self.store.db_path.read_bytes()).hexdigest(), before)
        damaged = self.store.home / 'damaged.zip'
        with zipfile.ZipFile(archive) as source, zipfile.ZipFile(damaged, 'w') as target:
            for info in source.infolist():
                data = source.read(info.filename)
                if info.filename == 'references/valid.png': data += b'corruption'
                target.writestr(info.filename, data)
        self.assertFalse(self.service.verify_backup(damaged)['ok'])
        self.assertEqual(hashlib.sha256(self.store.db_path.read_bytes()).hexdigest(), before)
        unsafe = self.store.home / 'unsafe.zip'
        with zipfile.ZipFile(archive) as source, zipfile.ZipFile(unsafe, 'w') as target:
            manifest = json.loads(source.read('backup_manifest.json'))
            manifest['files']['references/../escape.png'] = hashlib.sha256(b'bad').hexdigest()
            for info in source.infolist():
                data = (json.dumps(manifest).encode('utf-8') if info.filename == 'backup_manifest.json'
                        else source.read(info.filename))
                target.writestr(info.filename, data)
            target.writestr('references/../escape.png', b'bad')
        self.assertFalse(self.service.verify_backup(unsafe)['ok'])
        self.assertEqual(hashlib.sha256(self.store.db_path.read_bytes()).hexdigest(), before)

    def test_two_simultaneous_backup_upgrades_do_not_deadlock(self):
        barrier = threading.Barrier(2); outcomes = []
        def attempt():
            try:
                with self.store.operation():
                    barrier.wait(timeout=5)
                    self.service.create_backup('test')
                outcomes.append('success')
            except StorageError:
                outcomes.append('busy')
        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertCountEqual(outcomes, ['success', 'busy'])

    def test_verify_backup_v2_and_version_mismatch(self):
        self.store.save_data({'cases': [{'id': 'case1', 'body': '原文'}], 'modules': []})
        v1 = self.service.create_backup('v1')
        migrate(self.store)
        v2 = self.service.create_backup('v2')
        self.assertEqual(self.service.verify_backup(v2)['schema_version'], '2')
        self.assertFalse(self.service.verify_backup(v1)['ok'])
        self.assertEqual(self.store.get_setting('schema_version'), '2')

    def test_verify_video_and_missing_entries_without_restoring(self):
        refs = self.store.home / 'references'
        video = refs / 'result.mp4'
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-f', 'lavfi',
                        '-i', 'color=c=blue:s=16x16:d=0.2', '-pix_fmt', 'yuv420p',
                        '-y', str(video)], check=True, capture_output=True)
        self.store.save_data({'cases': [{'id': 'case1', 'body': '原文'}], 'modules': []})
        archive = self.service.create_backup('test')
        current = self.store.home / 'CURRENT'
        current.write_text('unchanged', encoding='utf-8')
        tracked = [self.store.db_path, self.store.legacy_json, video, current]
        digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
        before = {p: digest(p) for p in tracked}
        with patch.object(Storage, 'restore_full_backup', side_effect=AssertionError('restore invoked')), \
             patch.object(DataService, 'restore_backup', side_effect=AssertionError('restore invoked')):
            self.assertTrue(self.service.verify_backup(archive)['ok'])
            corrupted = self.store.home / 'corrupt-video.zip'
            with zipfile.ZipFile(archive) as source, zipfile.ZipFile(corrupted, 'w') as target:
                manifest = json.loads(source.read('backup_manifest.json'))
                bad = b'not a video despite a valid hash'
                manifest['files']['references/result.mp4'] = hashlib.sha256(bad).hexdigest()
                for info in source.infolist():
                    data = (json.dumps(manifest).encode('utf-8') if info.filename == 'backup_manifest.json'
                            else bad if info.filename == 'references/result.mp4' else source.read(info))
                    target.writestr(info, data)
            self.assertFalse(self.service.verify_backup(corrupted)['ok'])
            missing = self.store.home / 'missing-video.zip'
            with zipfile.ZipFile(archive) as source, zipfile.ZipFile(missing, 'w') as target:
                for info in source.infolist():
                    if info.filename != 'references/result.mp4':
                        target.writestr(info, source.read(info))
            self.assertFalse(self.service.verify_backup(missing)['ok'])
        self.assertEqual(before, {p: digest(p) for p in tracked})


if __name__ == '__main__': unittest.main()
