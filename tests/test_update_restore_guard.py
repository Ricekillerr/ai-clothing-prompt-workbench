import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import updater
from storage import Storage, migrate


class RestoreGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def fixture(self, name, version, marker):
        store = Storage(self.root / name)
        store.save_data({'cases': [{'id': 'case', 'body': marker}], 'modules': []})
        (store.home / 'references').mkdir(exist_ok=True)
        if version == 2: migrate(store)
        archive = store.create_full_backup(store.home / 'references')
        return store, archive

    @staticmethod
    def digest(store):
        return hashlib.sha256(store.db_path.read_bytes()).hexdigest()

    def test_matching_v1_and_v2(self):
        for version in (1, 2):
            with self.subTest(version=version):
                old, archive = self.fixture('old' + str(version), version, 'backup')
                active, _ = self.fixture('active' + str(version), version, 'active')
                updater.restore_snapshot(Storage, active.home, archive)
                self.assertEqual(Storage(active.home).load_data()['cases'][0]['body'], 'backup')
                self.assertFalse((active.home / updater.RESTORE_JOURNAL).exists())

    def test_v1_backup_cannot_replace_v2(self):
        old, archive = self.fixture('old', 1, 'backup')
        active, _ = self.fixture('active', 2, 'active')
        before = self.digest(active)
        with self.assertRaises(updater.UnsafeToRestore): updater.restore_snapshot(Storage, active.home, archive)
        self.assertEqual(self.digest(active), before)
        self.assertEqual(Storage(active.home).load_data()['cases'][0]['body'], 'active')
        self.assertTrue(archive.is_file())

    def test_corrupt_or_missing_version_rejected_without_replacement(self):
        old, archive = self.fixture('old', 1, 'backup')
        active, _ = self.fixture('active', 1, 'active')
        db = sqlite3.connect(active.db_path)
        try:
            db.execute("UPDATE metadata SET value='bad' WHERE key='schema_version'")
            db.commit()
        finally:
            db.close()
        before = self.digest(active)
        with self.assertRaises(updater.UnsafeToRestore): updater.restore_snapshot(Storage, active.home, archive)
        self.assertEqual(self.digest(active), before)
        self.assertTrue(archive.is_file())

    def test_unreadable_active_or_invalid_backup_preserves_database(self):
        old, archive = self.fixture('old', 1, 'backup')
        active, _ = self.fixture('active', 1, 'active')
        before = self.digest(active)
        bad = self.root / 'invalid.zip'
        bad.write_bytes(b'not a zip')
        with self.assertRaises(Exception): updater.restore_snapshot(Storage, active.home, bad)
        self.assertEqual(self.digest(active), before)
        self.assertEqual(Storage(active.home).load_data()['cases'][0]['body'], 'active')
        active.db_path.write_bytes(b'corrupt')
        before = self.digest(active)
        with self.assertRaises(updater.UnsafeToRestore): updater.restore_snapshot(Storage, active.home, archive)
        self.assertEqual(self.digest(active), before)
        self.assertEqual(active.db_path.read_bytes(), b'corrupt')
        self.assertTrue(archive.exists())

    def test_file_replacement_failure_rolls_back_and_preserves_backup(self):
        old, archive = self.fixture('old', 1, 'backup')
        active, _ = self.fixture('active', 1, 'active')
        before = self.digest(active)
        actual = updater.os.replace
        calls = []
        def fail_once(src, dst):
            if Path(src).parent == active.home or Path(dst).parent == active.home:
                calls.append(1)
                if len(calls) == 2: raise OSError('injected failure')
            return actual(src, dst)
        with mock.patch.object(updater.os, 'replace', side_effect=fail_once):
            with self.assertRaises(OSError): updater.restore_snapshot(Storage, active.home, archive)
        self.assertEqual(self.digest(active), before)
        self.assertEqual(Storage(active.home).load_data()['cases'][0]['body'], 'active')
        self.assertFalse((active.home / updater.RESTORE_JOURNAL).exists())
        self.assertTrue(archive.is_file())

    def test_interrupted_restore_can_be_recovered(self):
        old, archive = self.fixture('old', 1, 'backup')
        active, _ = self.fixture('active', 1, 'active')
        before = self.digest(active)
        actual = updater.os.replace
        count = []
        def fail(src, dst):
            if Path(src).parent == active.home or Path(dst).parent == active.home:
                count.append(1)
                if len(count) == 2: raise OSError('injected interruption')
            return actual(src, dst)
        with mock.patch.object(updater.os, 'replace', side_effect=fail), \
             mock.patch.object(updater, 'recover_interrupted_snapshot', side_effect=[False, RuntimeError('power loss')]):
            with self.assertRaises(RuntimeError): updater.restore_snapshot(Storage, active.home, archive)
        self.assertTrue((active.home / updater.RESTORE_JOURNAL).exists())
        self.assertTrue(updater.recover_interrupted_snapshot(active.home))
        self.assertEqual(self.digest(active), before)
        self.assertEqual(Storage(active.home).load_data()['cases'][0]['body'], 'active')
        self.assertTrue(archive.is_file())

    def test_process_exit_during_replacement_recovers_original(self):
        old, archive = self.fixture('old', 1, 'backup')
        active, _ = self.fixture('active', 1, 'active')
        before = self.digest(active)
        code = """import os,sys,updater
from pathlib import Path
from storage import Storage
home=Path(sys.argv[1]); original=updater.os.replace; calls=[]
def crash(src,dst):
    if Path(src).parent==home or Path(dst).parent==home:
        calls.append(1)
        if len(calls)==2: os._exit(71)
    return original(src,dst)
updater.os.replace=crash
updater.restore_snapshot(Storage,home,Path(sys.argv[2]))
"""
        result = subprocess.run([sys.executable, '-c', code, str(active.home), str(archive)], timeout=30)
        self.assertEqual(result.returncode, 71)
        self.assertTrue(updater.recover_interrupted_snapshot(active.home))
        self.assertEqual(self.digest(active), before)
        self.assertEqual(Storage(active.home).load_data()['cases'][0]['body'], 'active')


if __name__ == '__main__': unittest.main()
