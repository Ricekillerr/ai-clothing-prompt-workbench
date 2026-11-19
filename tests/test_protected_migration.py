import hashlib
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from protected_migration import create_protected_copy
from storage import Storage


class ProtectedCopyTests(unittest.TestCase):
    def test_old_restore_cannot_replace_v2_data_or_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            old_dir = parent / "legacy"
            old_dir.mkdir()
            old = Storage(old_dir)
            old.save_data({"cases": [{"id": "c1", "body": "原始提示词"}], "modules": []})
            refs = old_dir / "references"
            refs.mkdir()
            old_backup = old.create_full_backup(refs, app_version="0.6.5")
            old_backup_hash = hashlib.sha256(old_backup.read_bytes()).hexdigest()
            new_dir = parent / "protected"
            create_protected_copy(old, new_dir)
            v2 = Storage(new_dir)
            image = new_dir / "references" / "v2.png"
            image.write_bytes(b"v2-only")
            with v2._session() as con:
                con.execute("INSERT INTO garments(id,name,category) VALUES('g1','马甲','上装')")
                con.execute("INSERT INTO case_garments(id,case_id,garment_id) VALUES('cg1','c1','g1')")
                con.execute("INSERT INTO prompt_sources(id,case_id,version,body,sha256) VALUES('v2','c1',2,'新版','hash')")
            root = Path(__file__).resolve().parents[1]
            previous = subprocess.check_output(["git", "show", "74c9c28:storage.py"], cwd=root)
            previous_path = parent / "previous_storage.py"
            previous_path.write_bytes(previous)
            spec = importlib.util.spec_from_file_location("previous_storage_protected_test", previous_path)
            previous_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(previous_module)
            legacy = previous_module.Storage(old_dir)
            result = legacy.restore_full_backup(old_backup)
            self.assertTrue(result["ok"])
            self.assertEqual(hashlib.sha256(old_backup.read_bytes()).hexdigest(), old_backup_hash)
            with v2._session() as con:
                self.assertEqual(con.execute("SELECT count(*) FROM garments").fetchone()[0], 1)
                self.assertEqual(con.execute("SELECT count(*) FROM case_garments").fetchone()[0], 1)
                self.assertEqual(con.execute("SELECT count(*) FROM prompt_sources").fetchone()[0], 2)
            self.assertEqual(image.read_bytes(), b"v2-only")

    def test_failed_copy_does_not_replace_source_or_original_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = Storage(root / "legacy")
            source.save_data({"cases": [{"id": "c1", "body": "原始"}], "modules": []})
            old_backup = source.create_full_backup(source.home / "references")
            digest = hashlib.sha256(old_backup.read_bytes()).hexdigest()
            with patch("protected_migration.migrate", side_effect=RuntimeError("fault")):
                with self.assertRaisesRegex(RuntimeError, "fault"):
                    create_protected_copy(source, root / "protected")
            self.assertFalse((root / "protected").exists())
            self.assertEqual(source.load_data()["cases"][0]["body"], "原始")
            self.assertEqual(hashlib.sha256(old_backup.read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
