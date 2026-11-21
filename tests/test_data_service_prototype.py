import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from data_service_prototype import GenerationExperiment, UnsafeGeneration


class GenerationExperimentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source.db'
        db = sqlite3.connect(self.source)
        db.execute('CREATE TABLE cases(id TEXT PRIMARY KEY, body TEXT)')
        db.execute("INSERT INTO cases VALUES('case','original')")
        db.commit(); db.close()
        self.data = self.source.read_bytes()
        self.root_store = self.root / 'experiment'
        self.root_store.mkdir()
        self.engine = GenerationExperiment(self.root_store)
        self.asset = {'references/' + 'a' * 32 + '.png': b'synthetic-asset'}
        self.old = self.engine.install(self.data, self.asset)

    def test_idempotent_recovery_after_real_process_exit(self):
        program = """import os,sys
from pathlib import Path
from data_service_prototype import GenerationExperiment
root=Path(sys.argv[1]); db=Path(sys.argv[2]).read_bytes(); point=sys.argv[3]
def crash(name):
    if name==point: os._exit(71)
GenerationExperiment(root).install(db, {'references/'+'b'*32+'.png':b'new'}, crash)
"""
        for checkpoint in ('generation_ready', 'journal_prepared', 'pointer_switched', 'journal_committed'):
            with self.subTest(checkpoint=checkpoint):
                before = self.engine._pointer()
                result = subprocess.run([sys.executable, '-c', program, str(self.root_store), str(self.source), checkpoint], timeout=30)
                self.assertEqual(result.returncode, 71)
                result = self.engine.recover()
                actual = self.engine._pointer()
                if checkpoint == 'journal_committed': self.assertNotEqual(actual, before)
                else: self.assertEqual(actual, before)
                self.engine._validate(actual)
                self.assertEqual(self.engine.recover(), 'clean')
                self.assertEqual(self.engine._pointer(), actual)

    def test_interrupted_rollback_can_be_retried(self):
        def interrupt(name):
            if name == 'pointer_switched': raise RuntimeError('simulated stop')
        with self.assertRaises(RuntimeError): self.engine.install(self.data, {}, interrupt)
        def interrupt_recovery(name): raise RuntimeError('simulated recovery stop')
        with self.assertRaises(RuntimeError): self.engine.recover(interrupt_recovery)
        self.assertEqual(self.engine.recover(), 'recovered')
        self.assertEqual(self.engine._pointer(), self.old)

    def test_bad_identifier_and_corrupted_database_fail_closed(self):
        with self.assertRaises(UnsafeGeneration): self.engine.install(self.data, {'../escape.png': b'x'})
        with self.assertRaises(sqlite3.DatabaseError): self.engine.install(b'not sqlite', {})
        self.assertEqual(self.engine._pointer(), self.old)

    def test_tampering_with_active_asset_is_detected(self):
        (self.engine.generations / self.old / next(iter(self.asset))).write_bytes(b'changed')
        with self.assertRaises(UnsafeGeneration): self.engine.recover()

    def test_dispatch_rejects_paths_and_unknown_operations(self):
        self.assertEqual(self.engine.inspect('status', {})['generation_id'], self.old)
        for operation, args in [('read_file', {'path': '../secret'}), ('status', {'path': '../secret'}),
                                ('delete', {}), ('verify', {'id': 'other'})]:
            with self.subTest(operation=operation), self.assertRaises(UnsafeGeneration):
                self.engine.inspect(operation, args)


if __name__ == '__main__': unittest.main()
