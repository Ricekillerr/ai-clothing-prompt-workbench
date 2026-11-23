import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from storage import DataService, Storage, StorageError


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


if __name__ == '__main__': unittest.main()
