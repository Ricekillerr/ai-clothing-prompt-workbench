import hashlib
from io import BytesIO
from PIL import Image
import tempfile
import unittest
from pathlib import Path

from migration_preflight import inspect_database
from storage import Storage, StorageError
from stage2_schema import migrate


class MigrationPreflightTests(unittest.TestCase):
    def test_preflight_reads_old_data_without_changing_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Storage(root)
            store.save_data({"cases": [{"id": "c", "body": "原始提示词"}], "modules": []})
            image_dir = root / "references"
            image_dir.mkdir()
            buffer = BytesIO()
            Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")
            image_bytes = buffer.getvalue()
            (image_dir / "a.png").write_bytes(image_bytes)
            digest = hashlib.sha256(image_bytes).hexdigest()
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


    def test_image_formats_and_failure_classification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Storage(root)
            store.save_data({"cases": [{"id": "c", "body": "未改变"}], "modules": []})
            references = root / "references"
            references.mkdir()
            fixtures = []
            for extension, image_format in (("png", "PNG"), ("jpg", "JPEG"), ("webp", "WEBP")):
                out = BytesIO()
                Image.new("RGB", (3, 3), "blue").save(out, format=image_format)
                data = out.getvalue()
                name = "good." + extension
                (references / name).write_bytes(data)
                fixtures.append((name, hashlib.sha256(data).hexdigest(), "verified"))
            damaged = b"not an image"
            (references / "bad.png").write_bytes(damaged)
            fixtures.extend([
                ("bad.png", hashlib.sha256(damaged).hexdigest(), "corrupt"),
                ("missing.png", "0" * 64, "missing"),
                ("../escape.png", "0" * 64, "unsafe_path"),
                ("good.png", "0" * 64, "hash_mismatch"),
                ("good.jpg", None, "unverified"),
            ])
            with store._session() as con:
                for number, (name, digest, _) in enumerate(fixtures):
                    con.execute("INSERT INTO attachments(asset_id, relative_path, role, sha256) VALUES(?,?,?,?)",
                                ("c", name, str(number), digest))
            before = store.load_data()
            report = inspect_database(store.db_path, references)
            self.assertEqual([item["status"] for item in report["attachments"]],
                             [item[2] for item in fixtures])
            self.assertEqual(report["quick_check"], ["ok"])
            self.assertEqual(store.load_data(), before)

    def test_corrupt_matching_hash_blocks_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Storage(root)
            store.save_data({"cases": [{"id": "c", "body": "完整原文"}], "modules": []})
            references = root / "references"
            references.mkdir()
            data = b"not an image"
            (references / "bad.png").write_bytes(data)
            with store._session() as con:
                con.execute("INSERT INTO attachments(asset_id,relative_path,sha256) VALUES(?,?,?)",
                            ("c", "bad.png", hashlib.sha256(data).hexdigest()))
            with self.assertRaisesRegex(StorageError, "附件或外键"):
                migrate(store)
            with store._session() as con:
                self.assertEqual(con.execute(
                    "SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0], "1")
            self.assertEqual(store.load_data()["cases"][0]["body"], "完整原文")

    def test_symlinked_intermediate_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Storage(root)
            store.save_data({"cases": [{"id": "c", "body": "原文"}], "modules": []})
            references = root / "references"
            references.mkdir()
            inside = references / "real"
            inside.mkdir()
            link = references / "link"
            try:
                link.symlink_to(inside, target_is_directory=True)
            except OSError:
                self.skipTest("当前平台不允许创建符号链接")
            with store._session() as con:
                con.execute("INSERT INTO attachments(asset_id,relative_path,sha256) VALUES(?,?,?)",
                            ("c", "link/a.png", "0" * 64))
            self.assertEqual(inspect_database(store.db_path, references)["attachments"][0]["status"],
                             "unsafe_path")


if __name__ == "__main__":
    unittest.main()
