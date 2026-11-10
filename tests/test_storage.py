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

    def test_existing_attachment_link_survives_normal_save(self):
        store = Storage(self.home)
        store.save_data({"cases": [{"id": "c1", "body": "before"}], "modules": []})
        with store._session() as con:
            con.execute(
                "INSERT INTO attachments(asset_id,relative_path,media_type,role) VALUES(?,?,?,?)",
                ("c1", "references/example.png", "image", "reference"),
            )
        store.save_data({"cases": [{"id": "c1", "body": "after"}], "modules": []})
        with store._session() as con:
            row = con.execute("SELECT relative_path FROM attachments WHERE asset_id='c1'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["relative_path"], "references/example.png")

    def test_export_json_contains_round_trip_data(self):
        store = Storage(self.home)
        store.save_data({"cases": [{"id": "c1", "body": "中文提示词"}], "modules": []})
        output = self.home / "exports" / "backup.json"
        store.export_json(output)
        exported = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(exported["cases"][0]["body"], "中文提示词")

    def test_interrupted_first_migration_is_retried_on_next_start(self):
        source = self.write_legacy({
            "cases": [{"id": "c-retry", "body": "恢复迁移"}],
            "modules": [{"id": "m-retry", "body": "模块"}],
        })
        # Simulate a database whose schema exists but migration marker was not committed.
        import sqlite3
        db = self.home / "prompt_library.sqlite3"
        con = sqlite3.connect(db)
        con.executescript("""
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE assets (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL CHECK(kind IN ('case','module')),
                payload_json TEXT NOT NULL,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                relative_path TEXT NOT NULL,
                media_type TEXT NOT NULL DEFAULT 'image',
                role TEXT NOT NULL DEFAULT 'reference',
                sha256 TEXT,
                created_at TEXT,
                UNIQUE(asset_id, relative_path, role)
            );
            INSERT INTO metadata(key,value) VALUES('schema_version','1');
        """)
        con.commit()
        con.close()
        store = Storage(self.home, legacy_json=source)
        loaded = store.load_data()
        self.assertEqual(loaded["cases"][0]["id"], "c-retry")
        self.assertEqual(loaded["modules"][0]["id"], "m-retry")

    def test_duplicate_ids_are_rejected_on_save(self):
        store = Storage(self.home)
        with self.assertRaises(StorageError):
            store.save_data({
                "cases": [{"id": "same"}],
                "modules": [{"id": "same"}],
            })
        self.assertEqual(store.load_data()["cases"], [])

    def test_scan_source_and_ingest_queue_preserve_approved_unchanged_file(self):
        store = Storage(self.home)
        source_dir = self.home / "history"
        source_dir.mkdir()
        source_id = store.register_scan_source(str(source_dir))
        item = {
            "path": str(source_dir / "sample.png"),
            "relative_path": "sample.png",
            "name": "sample.png",
            "extension": ".png",
            "media_type": "image",
            "size": 12,
            "modified_ns": 123,
            "sha256": "a" * 64,
        }
        first = store.save_scan_results(source_id, [item])
        self.assertEqual(first["added"], 1)
        self.assertEqual(len(store.list_pending_ingest(source_id)), 1)
        store.set_ingest_status(item["path"], "approved")
        second = store.save_scan_results(source_id, [item])
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(store.list_pending_ingest(source_id), [], "已审核且内容未变化的文件不应重新进入待审核队列")

    def test_changed_ingest_content_returns_to_pending(self):
        store = Storage(self.home)
        source_dir = self.home / "history"
        source_dir.mkdir()
        source_id = store.register_scan_source(str(source_dir))
        item = {
            "path": str(source_dir / "prompt.txt"),
            "relative_path": "prompt.txt",
            "name": "prompt.txt",
            "extension": ".txt",
            "media_type": "text",
            "size": 3,
            "modified_ns": 1,
            "sha256": "b" * 64,
        }
        store.save_scan_results(source_id, [item])
        store.set_ingest_status(item["path"], "ignored")
        changed = dict(item, size=4, modified_ns=2, sha256="c" * 64)
        result = store.save_scan_results(source_id, [changed])
        self.assertEqual(result["changed"], 1)
        self.assertEqual(len(store.list_pending_ingest(source_id)), 1)

    def test_ai_analysis_requires_human_approval_before_asset_import(self):
        store = Storage(self.home)
        source_dir = self.home / "history"
        source_dir.mkdir()
        source_id = store.register_scan_source(str(source_dir))
        prompt = str(source_dir / "look.txt")
        image = str(source_dir / "look.png")
        analysis_id = store.save_ai_analysis(
            prompt, image, "p" * 64, "i" * 64, "test-model",
            {"title": "Look test", "category": "服装", "tags": ["黑色"],
             "prompt_text": "保持人物不变，仅替换服装", "prompt_summary": "局部换装",
             "confidence": 0.92, "risk_notes": []},
        )
        self.assertEqual(len(store.list_ai_analyses("review")), 1)
        self.assertEqual(store.load_data()["cases"], [])
        asset_id = store.approve_ai_analysis(analysis_id)
        cases = store.load_data()["cases"]
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["id"], asset_id)
        self.assertEqual(cases[0]["body"], "保持人物不变，仅替换服装")
        self.assertEqual(store.list_ai_analyses("review"), [])
        self.assertEqual(store.list_ai_analyses("approved")[0]["approved_asset_id"], asset_id)

    def test_ai_analysis_can_be_rejected_without_import(self):
        store = Storage(self.home)
        analysis_id = store.save_ai_analysis(
            "prompt.txt", "asset.png", "p" * 64, "i" * 64, "test-model",
            {"title": "Rejected", "prompt_text": "text"},
        )
        store.reject_ai_analysis(analysis_id)
        self.assertEqual(store.load_data()["cases"], [])
        self.assertEqual(store.list_ai_analyses("rejected")[0]["id"], analysis_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
