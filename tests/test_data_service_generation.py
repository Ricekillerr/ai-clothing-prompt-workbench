import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import imageio_ffmpeg
from PIL import Image
from storage import DataService, Storage, StorageError


class DataServiceGenerationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.store = Storage(self.home)
        refs = self.home / 'references'
        refs.mkdir()
        Image.new('RGB', (8, 8), 'blue').save(refs / 'image.png')
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-f', 'lavfi', '-i',
                        'color=c=blue:s=16x16:d=0.2', '-pix_fmt', 'yuv420p', '-y', str(refs / 'video.mp4')],
                       check=True, capture_output=True)
        self.store.save_data({'cases': [{'id': 'c1', 'body': '原文', 'image': 'image.png'}], 'modules': []})
        self.service = DataService(self.store)

    def test_create_activate_verify_and_corruption(self):
        first = self.service.create_generation()
        self.assertEqual(self.service.verify_generation(first)['schema_version'], '1')
        folder = self.home / 'generations' / ('generation_' + first)
        self.assertTrue((folder / 'data' / 'prompt_library.json').is_file())
        self.assertTrue((folder / 'references' / 'video.mp4').is_file())
        self.assertEqual(self.service.activate_generation(first), first)
        second = self.service.create_generation()
        self.assertEqual(self.service.activate_generation(second), second)
        self.assertEqual(self.service.activate_generation(second), second)
        self.assertTrue(folder.is_dir())
        (folder / 'references' / 'image.png').write_bytes(b'damaged')
        with self.assertRaises(StorageError):
            self.service.activate_generation(first)
        self.assertEqual(self.service.get_current_generation(), second)

    def test_activation_crash_and_recovery_crash_before_initialization(self):
        first = self.service.create_generation()
        self.service.activate_generation(first)
        second = self.service.create_generation()
        activate = '''import os,sys
from pathlib import Path
from storage import Storage,DataService
service=DataService(Storage(Path(sys.argv[1])))
original=DataService._generation_atomic_write
def crash(path,data):
    original(path,data)
    if path.name == "CURRENT": os._exit(71)
DataService._generation_atomic_write=staticmethod(crash)
service.activate_generation(sys.argv[2])
'''
        recover = '''import os,sys
from pathlib import Path
from storage import Storage,DataService
original=DataService._generation_atomic_write
def crash(path,data):
    original(path,data)
    if path.name == "CURRENT": os._exit(72)
DataService._generation_atomic_write=staticmethod(crash)
Storage(Path(sys.argv[1]))
'''
        self.assertEqual(subprocess.run([sys.executable, '-c', activate, str(self.home), second], timeout=60).returncode, 71)
        self.assertEqual(subprocess.run([sys.executable, '-c', recover, str(self.home)], timeout=60).returncode, 72)
        reopened = DataService(Storage(self.home))
        self.assertEqual(reopened.get_current_generation(), first)
        self.assertEqual(reopened.recover_pending_generation(), first)
        self.assertEqual(reopened.recover_pending_generation(), first)
        self.assertTrue((self.home / 'generations' / ('generation_' + second)).is_dir())

    def test_committed_interruption_retains_new_generation(self):
        first = self.service.create_generation()
        self.service.activate_generation(first)
        second = self.service.create_generation()
        program = '''import os,sys,json
from pathlib import Path
from storage import Storage,DataService
service=DataService(Storage(Path(sys.argv[1])))
original=DataService._generation_atomic_write
def crash(path,data):
    original(path,data)
    if path.name == "transaction.json" and json.loads(data)["state"] == "COMMITTED": os._exit(73)
DataService._generation_atomic_write=staticmethod(crash)
service.activate_generation(sys.argv[2])
'''
        self.assertEqual(subprocess.run([sys.executable, '-c', program, str(self.home), second], timeout=60).returncode, 73)
        reopened = DataService(Storage(self.home))
        self.assertEqual(reopened.get_current_generation(), second)
        self.assertEqual(reopened.recover_pending_generation(), second)

    def test_creation_interruption_preserves_previous_pointer(self):
        first = self.service.create_generation()
        self.service.activate_generation(first)
        program = '''import os,sys
from pathlib import Path
from storage import Storage,DataService
service=DataService(Storage(Path(sys.argv[1])))
original=DataService._generation_atomic_write
def crash(path,data):
    if path.name == "manifest.json": os._exit(74)
    original(path,data)
DataService._generation_atomic_write=staticmethod(crash)
service.create_generation()
'''
        self.assertEqual(subprocess.run([sys.executable, '-c', program, str(self.home)], timeout=60).returncode, 74)
        reopened = DataService(Storage(self.home))
        self.assertEqual(reopened.get_current_generation(), first)
        self.assertEqual(reopened.recover_pending_generation(), first)

    def test_failed_pointer_write_rolls_back(self):
        first = self.service.create_generation()
        self.service.activate_generation(first)
        second = self.service.create_generation()
        original = DataService._generation_atomic_write
        failed = False
        def fail_once(path, data):
            nonlocal failed
            if path.name == 'CURRENT' and not failed:
                failed = True
                raise OSError('injected failure')
            return original(path, data)
        with patch.object(DataService, '_generation_atomic_write', side_effect=fail_once):
            with self.assertRaises(OSError):
                self.service.activate_generation(second)
        self.assertEqual(self.service.get_current_generation(), first)


    def test_formal_generation_crash_matrix(self):
        first = self.service.create_generation()
        self.service.activate_generation(first)
        candidate = self.service.create_generation()
        root = self.home / 'generations'
        old_folder = root / ('generation_' + first)
        original = {p.relative_to(old_folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in old_folder.rglob('*') if p.is_file()}
        program = r"""import os,sys,json
from pathlib import Path
from storage import Storage,DataService
home=Path(sys.argv[1]); target=sys.argv[2]; point=sys.argv[3]
service=DataService(Storage(home))
original_mkdir=Path.mkdir
original_backup=Storage.backup
original_write_text=Path.write_text
original_open=Path.open
original_atomic=DataService._generation_atomic_write
original_validate=DataService._validate_generation_at.__func__
original_replace=os.replace

def mkdir(path,*args,**kwargs):
    result=original_mkdir(path,*args,**kwargs)
    if point == 'directory' and path.name.startswith('generation_'): os._exit(81)
    return result
Path.mkdir=mkdir

def backup(self,*args,**kwargs):
    result=original_backup(self,*args,**kwargs)
    if point == 'sqlite': os._exit(81)
    return result
Storage.backup=backup

def write_text(path,*args,**kwargs):
    result=original_write_text(path,*args,**kwargs)
    if point == 'json' and path.name == 'prompt_library.json': os._exit(81)
    return result
Path.write_text=write_text

class CrashWriter:
    def __init__(self,stream): self.stream=stream
    def __enter__(self): self.stream.__enter__(); return self
    def __exit__(self,*args): return self.stream.__exit__(*args)
    def write(self,data):
        self.stream.write(data); self.stream.flush(); os._exit(81)
    def __getattr__(self,name): return getattr(self.stream,name)
def open_file(path,*args,**kwargs):
    stream=original_open(path,*args,**kwargs)
    mode=args[0] if args else kwargs.get('mode','r')
    if point in ('image','video') and mode == 'wb' and path.name == ('image.png' if point == 'image' else 'video.mp4') and 'generation_' in str(path):
        return CrashWriter(stream)
    return stream
Path.open=open_file

def atomic(path,data):
    result=original_atomic(path,data)
    if point == 'manifest' and path.name == 'manifest.json': os._exit(81)
    if point == 'committed' and path.name == 'transaction.json' and json.loads(data)['state'] == 'COMMITTED': os._exit(81)
    return result
DataService._generation_atomic_write=staticmethod(atomic)

def validate(cls,home,ident):
    result=original_validate(cls,home,ident)
    if point == 'verified' and ident not in (target, sys.argv[4]): os._exit(81)
    return result
DataService._validate_generation_at=classmethod(validate)

def replace(src,dst):
    if Path(dst).name == 'CURRENT' and point == 'current_temp': os._exit(81)
    result=original_replace(src,dst)
    if Path(dst).name == 'CURRENT' and point == 'current_replaced': os._exit(81)
    return result
os.replace=replace
if point in ('directory','sqlite','json','image','video','manifest','verified'):
    service.create_generation()
else:
    service.activate_generation(target)
"""
        for point in ('directory', 'sqlite', 'json', 'image', 'video', 'manifest', 'verified',
                      'current_temp', 'current_replaced', 'committed'):
            with self.subTest(point=point):
                self.service.activate_generation(first)
                result = subprocess.run([sys.executable, '-c', program, str(self.home), candidate, point, first], timeout=90)
                self.assertEqual(result.returncode, 81, point)
                restarted = DataService(Storage(self.home))
                expected = candidate if point == 'committed' else first
                self.assertEqual(restarted.recover_pending_generation(), expected)
                self.assertEqual(restarted.recover_pending_generation(), expected)
                restarted.verify_generation(expected)
                self.assertEqual(original, {p.relative_to(old_folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                            for p in old_folder.rglob('*') if p.is_file()})
                self.assertTrue((root / ('generation_' + candidate)).is_dir())


    def test_online_backup_and_candidate_json_abrupt_exit(self):
        # Enough distinct pages for SQLite's progress callback to observe an
        # unfinished online backup before the child terminates.
        self.store.save_data({'cases': [{'id': 'large', 'body': 'abcdefgh' * 300000,
                                         'image': 'image.png'}], 'modules': []})
        first = self.service.create_generation()
        self.service.activate_generation(first)
        root = self.home / 'generations'
        old = root / ('generation_' + first)
        baseline = {p.relative_to(old).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in old.rglob('*') if p.is_file()}
        program = r"""import os,sys,sqlite3
from pathlib import Path
from storage import Storage,DataService
home=Path(sys.argv[1]); point=sys.argv[2]
service=DataService(Storage(home))
if point == 'sqlite':
    original=sqlite3.connect
    class InterruptConnection(sqlite3.Connection):
        def backup(self,target,*args,**kwargs):
            def progress(status,remaining,total):
                if remaining > 0 and total > 1: os._exit(86)
            return super().backup(target,pages=1,progress=progress)
    def connect(*args,**kwargs):
        kwargs['factory']=InterruptConnection
        return original(*args,**kwargs)
    sqlite3.connect=connect
elif point == 'json_write':
    original=Path.open
    class InterruptedWrite:
        def __init__(self,stream): self.stream=stream
        def __enter__(self): self.stream.__enter__(); return self
        def __exit__(self,*args): return self.stream.__exit__(*args)
        def write(self,data):
            self.stream.write(data[:max(1,len(data)//2)]); self.stream.flush(); os._exit(86)
        def __getattr__(self,name): return getattr(self.stream,name)
    def open_file(path,*args,**kwargs):
        stream=original(path,*args,**kwargs)
        mode=args[0] if args else kwargs.get('mode','r')
        if mode == 'wb' and path.name.startswith('.prompt_library.json.') and 'generation_' in str(path):
            return InterruptedWrite(stream)
        return stream
    Path.open=open_file
elif point == 'json_replaced':
    original=os.replace
    def replace(src,dst):
        result=original(src,dst)
        if Path(dst).name == 'prompt_library.json' and 'generation_' in str(dst): os._exit(86)
        return result
    os.replace=replace
service.create_generation()
"""
        for point in ('sqlite', 'json_write', 'json_replaced'):
            with self.subTest(point=point):
                before = set(root.glob('generation_*'))
                result = subprocess.run([sys.executable, '-c', program, str(self.home), point], timeout=90)
                self.assertEqual(result.returncode, 86, point)
                reopened = DataService(Storage(self.home))
                self.assertEqual(reopened.recover_pending_generation(), first)
                self.assertEqual(reopened.recover_pending_generation(), first)
                reopened.verify_generation(first)
                self.assertEqual(baseline, {p.relative_to(old).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                            for p in old.rglob('*') if p.is_file()})
                for partial in set(root.glob('generation_*')) - before:
                    with self.assertRaises(StorageError):
                        reopened.activate_generation(partial.name.removeprefix('generation_'))
                complete = reopened.create_generation()
                reopened.verify_generation(complete)
                self.assertEqual(reopened.get_current_generation(), first)


if __name__ == '__main__': unittest.main()
