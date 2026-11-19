import sqlite3
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
