import hashlib
import tempfile
import unittest
from pathlib import Path

from migration_preflight import inspect_database
from storage import Storage


class MigrationPreflightTests(unittest.TestCase):
    def test_preflight_reads_old_data_without_changing_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Storage(root)
            store.save_data({"cases": [{"id": "c", "body": "原始提示词"}], "modules": []})
            image_dir = root / "references"
            image_dir.mkdir()
            (image_dir / "a.png").write_bytes(b"image")
            digest = hashlib.sha256(b"image").hexdigest()
            with store._session() as con:
                con.execute("INSERT INTO attachments(asset_id,relative_path,sha256) VALUES(?,?,?)",
                            ("c", "a.png", digest))
            before = store.load_data()
            result = inspect_database(store.db_path, image_dir)
            self.assertEqual(result["integrity"], ["ok"])
            self.assertEqual(result["foreign_key_errors"], [])
            self.assertEqual(result["counts"]["assets"], 1)
            self.assertEqual(result["attachments"][0]["status"], "verified")
            self.assertEqual(result["prompt_hashes"][0]["sha256"],
                             hashlib.sha256("原始提示词".encode()).hexdigest())
            self.assertEqual(store.load_data(), before)


if __name__ == "__main__":
    unittest.main()
