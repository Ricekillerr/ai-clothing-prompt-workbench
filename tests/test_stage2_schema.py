import importlib.util
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import stage2_schema
from storage import Storage

class Stage2SchemaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Storage(Path(self.tmp.name))
        self.store.save_data({"cases": [{"id": "c1", "body": "原始提示词"}], "modules": []})

    def test_dimensions_use_semantics(self):
        self.assertEqual(stage2_schema.map_dimension("上身效果"), "fit_on_body")
        self.assertEqual(stage2_schema.map_dimension("反光效果"), "reflectivity")
        self.assertIsNone(stage2_schema.map_dimension("06"))
        self.assertEqual(len(stage2_schema.DIMENSIONS), 10)

    def test_migration_is_idempotent_and_source_immutable(self):
        result = stage2_schema.migrate(self.store)
        self.assertEqual(result["status"], "migrated")
        self.assertTrue(Path(result["backup"]).exists())
        self.assertEqual(stage2_schema.migrate(self.store)["status"], "already_migrated")
        with self.store._session() as con:
            row = con.execute("SELECT body FROM prompt_sources").fetchone()
            self.assertEqual(row["body"], "原始提示词")
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("UPDATE prompt_sources SET body='edited'")
        self.assertEqual(self.store.load_data()["cases"][0]["body"], "原始提示词")

    def test_segments_must_trace_to_original_unicode(self):
        stage2_schema.migrate(self.store)
        with self.store._session() as con:
            source = con.execute("SELECT id FROM prompt_sources").fetchone()[0]
            con.execute("INSERT INTO prompt_segments(id,source_id,dimension,original_text,source_start,source_end) VALUES(?,?,?,?,?,?)",
                        ("s1", source, "fabric", "原始", 0, 2))
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("INSERT INTO prompt_segments(id,source_id,dimension,original_text,source_start,source_end) VALUES(?,?,?,?,?,?)",
                            ("s2", source, "fabric", "改写", 0, 2))
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("UPDATE prompt_segments SET source_end=3 WHERE id='s1'")

    def test_older_sqlite_connection_cannot_write_assets(self):
        stage2_schema.migrate(self.store)
        old = sqlite3.connect(str(self.store.db_path))
        try:
            with self.assertRaises(sqlite3.OperationalError):
                old.execute("UPDATE assets SET payload_json='{}' WHERE id='c1'")
            old.rollback()
        finally:
            old.close()
        self.assertEqual(self.store.load_data()["cases"][0]["body"], "原始提示词")
        self.store.save_data({"cases": [{"id": "c1", "body": "保留原始版本的新文本"}], "modules": []})
        with self.store._session() as con:
            self.assertEqual(con.execute("SELECT body FROM prompt_sources").fetchone()[0], "原始提示词")

    def test_actual_previous_storage_cannot_save(self):
        stage2_schema.migrate(self.store)
        root = Path(__file__).resolve().parents[1]
        previous = subprocess.check_output(
            ["git", "show", "74c9c28:storage.py"], cwd=root)
        old_path = Path(self.tmp.name) / "old_storage.py"
        old_path.write_bytes(previous)
        spec = importlib.util.spec_from_file_location("stage1_storage_fixture", old_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        old_store = module.Storage(Path(self.tmp.name))
        with self.assertRaises(module.StorageError):
            old_store.save_data({"cases": [{"id": "c1", "body": "覆盖"}], "modules": []})
        self.assertEqual(self.store.load_data()["cases"][0]["body"], "原始提示词")

    def test_v1_backup_cannot_overwrite_live_v2_data(self):
        references = self.store.home / "references"
        references.mkdir()
        image = references / "current.png"
        image.write_bytes(b"current image")
        old_backup = self.store.create_full_backup(references, app_version="0.6.5")
        stage2_schema.migrate(self.store)
        with self.store._session() as con:
            con.execute("INSERT INTO garments(id,name,category) VALUES('g1','马甲','上装')")
        with self.assertRaisesRegex(Exception, "不能使用旧版本备份覆盖 V2"):
            self.store.restore_full_backup(old_backup)
        with self.store._session() as con:
            self.assertEqual(con.execute("SELECT name FROM garments WHERE id='g1'").fetchone()[0], "马甲")
        self.assertEqual(image.read_bytes(), b"current image")
        self.assertEqual(self.store.load_data()["cases"][0]["body"], "原始提示词")

    def test_unknown_future_schema_is_rejected_before_writes(self):
        with self.store._session() as con:
            con.execute("UPDATE metadata SET value='999' WHERE key='schema_version'")
        before = self.store.db_path.read_bytes()
        with self.assertRaisesRegex(Exception, "版本不受"):
            Storage(self.store.home)
        self.assertEqual(self.store.db_path.read_bytes(), before)

    def test_ddl_failure_rolls_back(self):
        original = list(stage2_schema.SCHEMA)
        with patch.object(stage2_schema, "SCHEMA", original + ["invalid sql"]):
            with self.assertRaises(sqlite3.Error):
                stage2_schema.migrate(self.store)
        with self.store._session() as con:
            self.assertIsNone(con.execute("SELECT name FROM sqlite_master WHERE name='garments'").fetchone())
            self.assertEqual(con.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0], "1")
        self.assertEqual(self.store.load_data()["cases"][0]["body"], "原始提示词")

if __name__ == "__main__":
    unittest.main()
