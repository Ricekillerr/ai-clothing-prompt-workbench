# -*- coding: utf-8 -*-
"""Offline regression tests for Prompt Atelier storage.py."""
import json
import tempfile
import unittest
from pathlib import Path

from storage import Storage, StorageError


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "profile"
        self.home.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write_legacy(self, payload):
        path = self.home / "prompt_library.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_migrates_cases_and_modules_without_modifying_source(self):
        original = {"version": 2, "cases": [{"id": "c1", "name": "case", "body": "提示词"}],
                    "modules": [{"id": "m1", "name": "fabric", "body": "面料"}]}
        path = self.write_legacy(original)
        before = path.read_bytes()
        store = Storage(self.home)
        loaded = store.load_data()
        self.assertEqual([x["id"] for x in loaded["cases"]], ["c1"])
        self.assertEqual([x["id"] for x in loaded["modules"]], ["m1"])
        self.assertEqual(path.read_bytes(), before)

    def test_corrupt_json_does_not_create_empty_database(self):
        path = self.home / "prompt_library.json"
        path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(StorageError):
            Storage(self.home)
        self.assertFalse((self.home / "prompt_library.sqlite3").exists())
        self.assertTrue(path.exists())

    def test_duplicate_or_missing_ids_are_rejected_before_database_creation(self):
        for payload in (
            {"cases": [{"id": "x"}], "modules": [{"id": "x"}]},
            {"cases": [{"name": "missing"}], "modules": []},
        ):
            with self.subTest(payload=payload):
                self.write_legacy(payload)
                db = self.home / "prompt_library.sqlite3"
                if db.exists():
                    db.unlink()
                with self.assertRaises(StorageError):
                    Storage(self.home)
                self.assertFalse(db.exists())

    def test_save_load_round_trip_and_backup(self):
        store = Storage(self.home)
        payload = {"cases": [{"id": "c1", "name": "test", "body": "original"}],
                   "modules": [{"id": "m1", "name": "module", "body": "fabric"}]}
        store.save_data(payload)
        self.assertEqual(store.load_data()["cases"][0]["body"], "original")
        backup = store.backup()
        self.assertTrue(backup.exists())

    def test_invalid_save_does_not_delete_existing_data(self):
        store = Storage(self.home)
        store.save_data({"cases": [{"id": "c1", "body": "safe"}], "modules": []})
        with self.assertRaises(StorageError):
            store.save_data({"cases": [{"body": "missing id"}], "modules": []})
        self.assertEqual(store.load_data()["cases"][0]["body"], "safe")


if __name__ == "__main__":
    unittest.main(verbosity=2)
