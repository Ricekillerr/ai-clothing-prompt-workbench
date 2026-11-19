import hashlib
import json
import sys
import os
import time
from urllib.request import build_opener, ProxyHandler
import updater
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from protected_migration import create_protected_copy, recover_protected_copy
from storage import Storage, StorageError, active_data_home


class ProtectedCopyTests(unittest.TestCase):
    def test_selection_requires_completed_valid_v2_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp)
            legacy = profile / "AI_Prompt_Asset_Workbench"
            source = Storage(legacy)
            source.save_data({"cases": [{"id": "c1", "body": "原文"}], "modules": []})
            self.assertEqual(active_data_home(profile), legacy)
            container = profile / "AI_Prompt_Asset_Workbench_v2"
            create_protected_copy(source, container)
            self.assertEqual(active_data_home(profile), container / "data")
            (container / "stage2-ready.json").write_text("bad json", encoding="utf8")
            with self.assertRaises(StorageError):
                active_data_home(profile)

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
            result = create_protected_copy(old, new_dir)
            self.assertTrue(Path(result["migration"]["backup"]).is_file())
            v2 = Storage(new_dir / "data")
            image = new_dir / "data" / "references" / "v2.png"
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

    def test_updater_rejects_incompatible_old_program_with_active_v2(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp)
            legacy = Storage(profile / "AI_Prompt_Asset_Workbench")
            legacy.save_data({"cases": [{"id": "c1", "body": "原文"}], "modules": []})
            protected = profile / "AI_Prompt_Asset_Workbench_v2"
            create_protected_copy(legacy, protected)
            v2 = Storage(protected / "data")
            with v2._session() as con:
                con.execute("INSERT INTO garments(id,name,category) VALUES('g1','外套','上装')")
            program = profile / "program"
            program.mkdir()
            root = Path(__file__).resolve().parents[1]
            for name in ("storage.py", "ai_prompt_workbench.py"):
                (program / name).write_bytes(subprocess.check_output(
                    ["git", "show", "74c9c28:" + name], cwd=root))
            stage = profile / "stage"
            stage.mkdir()
            (stage / "index.html").write_bytes(b"new")
            (stage / "update_meta.json").write_text(
                json.dumps({"version": "0.7.0", "files": {
                    "index.html": hashlib.sha256(b"new").hexdigest()}}), encoding="utf8")
            args = ["updater.py", str(stage), str(program), str(program / "launcher.bat")]
            with patch.object(sys, "argv", args), patch.object(updater.Path, "home", return_value=profile), \
                 patch.object(updater, "service_running", return_value=False):
                self.assertEqual(updater.main(), 9)
            self.assertFalse((program / "index.html").exists())
            self.assertTrue(stage.exists())
            with v2._session() as con:
                self.assertEqual(con.execute("SELECT name FROM garments").fetchone()[0], "外套")

    def test_running_workbench_reads_protected_v2_after_old_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp)
            legacy_dir = profile / "AI_Prompt_Asset_Workbench"
            old = Storage(legacy_dir)
            old.save_data({"cases": [{"id": "c1", "body": "旧原文"}], "modules": []})
            old_backup = old.create_full_backup(legacy_dir / "references")
            protected = profile / "AI_Prompt_Asset_Workbench_v2"
            create_protected_copy(old, protected)
            v2 = Storage(protected / "data")
            v2.save_data({"cases": [{"id": "c1", "body": "V2 新原文"}], "modules": []})
            root = Path(__file__).resolve().parents[1]
            previous = subprocess.check_output(["git", "show", "74c9c28:storage.py"], cwd=root)
            path = profile / "legacy_storage.py"
            path.write_bytes(previous)
            spec = importlib.util.spec_from_file_location("old_storage_running_test", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            env = dict(os.environ, HOME=str(profile), USERPROFILE=str(profile),
                       PROMPT_ATELIER_NO_BROWSER="1", PYTHONDONTWRITEBYTECODE="1")
            process = subprocess.Popen([sys.executable, str(root / "ai_prompt_workbench.py")],
                                       cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            opener = build_opener(ProxyHandler({}))
            try:
                deadline = time.monotonic() + 15
                while True:
                    if process.poll() is not None:
                        self.fail("隔离工作台未能启动")
                    try:
                        with opener.open("http://127.0.0.1:8765/api/data", timeout=1) as response:
                            data = json.loads(response.read())
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            self.fail("隔离工作台启动超时")
                        time.sleep(0.1)
                self.assertEqual(data["cases"][0]["body"], "V2 新原文")
                module.Storage(legacy_dir).restore_full_backup(old_backup)
                with opener.open("http://127.0.0.1:8765/api/data", timeout=2) as response:
                    after = json.loads(response.read())
                self.assertEqual(after["cases"][0]["body"], "V2 新原文")
            finally:
                process.terminate()
                process.wait(timeout=10)

    def test_previous_updater_recovery_cannot_replace_v2_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            old_dir = parent / "legacy"
            old_dir.mkdir()
            old = Storage(old_dir)
            old.save_data({"cases": [{"id": "c1", "body": "原文"}], "modules": []})
            backup = old.create_full_backup(old_dir / "references", app_version="0.6.5")
            digest = hashlib.sha256(backup.read_bytes()).hexdigest()
            protected = parent / "protected"
            create_protected_copy(old, protected)
            v2 = Storage(protected / "data")
            with v2._session() as con:
                con.execute("INSERT INTO garments(id,name,category) VALUES('g1','外套','上装')")
                con.execute("INSERT INTO case_garments(id,case_id,garment_id) VALUES('cg1','c1','g1')")
                con.execute("INSERT INTO prompt_sources(id,case_id,version,body,sha256) VALUES('v2','c1',2,'新版本','hash')")
            root = Path(__file__).resolve().parents[1]
            modules = []
            for name in ("storage", "updater"):
                path = parent / (name + "_previous.py")
                path.write_bytes(subprocess.check_output(["git", "show", "74c9c28:" + name + ".py"], cwd=root))
                spec = importlib.util.spec_from_file_location("previous_" + name, path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                modules.append(module)
            modules[1].restore_snapshot(modules[0].Storage, old_dir, backup)
            self.assertEqual(hashlib.sha256(backup.read_bytes()).hexdigest(), digest)
            with v2._session() as con:
                self.assertEqual(con.execute("SELECT count(*) FROM garments").fetchone()[0], 1)
                self.assertEqual(con.execute("SELECT count(*) FROM case_garments").fetchone()[0], 1)
                self.assertEqual(con.execute("SELECT count(*) FROM prompt_sources").fetchone()[0], 2)

    def test_interrupted_activation_leaves_unready_container_and_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = Storage(root / "legacy")
            source.save_data({"cases": [{"id": "c1", "body": "原始"}], "modules": []})
            destination = root / "protected"
            with patch("storage.os.rename", side_effect=OSError("injected interruption")):
                with self.assertRaisesRegex(OSError, "injected interruption"):
                    create_protected_copy(source, destination)
            self.assertTrue(destination.is_dir())
            self.assertFalse((destination / "stage2-ready.json").exists())
            staged = list(root.glob(".stage2-copy-*"))
            self.assertEqual(len(staged), 1)
            self.assertEqual(source.load_data()["cases"][0]["body"], "原始")
            recovered = recover_protected_copy(destination, staged[0])
            self.assertEqual(recovered["status"], "recovered")
            recovered_store = Storage(destination / "data")
            self.assertEqual(recovered_store.load_data()["cases"][0]["body"], "原始")
            with self.assertRaises(StorageError):
                recover_protected_copy(destination, staged[0])

    def test_recovery_after_data_move_before_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = Storage(root / "legacy")
            source.save_data({"cases": [{"id": "c1", "body": "原始"}], "modules": []})
            destination = root / "protected"
            with patch("storage._write_ready_marker", side_effect=OSError("marker interruption")):
                with self.assertRaisesRegex(OSError, "marker interruption"):
                    create_protected_copy(source, destination)
            self.assertTrue((destination / "data" / "prompt_library.sqlite3").is_file())
            self.assertFalse((destination / "stage2-ready.json").exists())
            missing_stage = root / ".stage2-copy-moved"
            self.assertEqual(recover_protected_copy(destination, missing_stage)["status"], "recovered")
            self.assertEqual(Storage(destination / "data").load_data()["cases"][0]["body"], "原始")

    def test_failed_copy_does_not_replace_source_or_original_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = Storage(root / "legacy")
            source.save_data({"cases": [{"id": "c1", "body": "原始"}], "modules": []})
            old_backup = source.create_full_backup(source.home / "references")
            digest = hashlib.sha256(old_backup.read_bytes()).hexdigest()
            with patch("storage.migrate", side_effect=RuntimeError("fault")):
                with self.assertRaisesRegex(RuntimeError, "fault"):
                    create_protected_copy(source, root / "protected")
            self.assertFalse((root / "protected").exists())
            self.assertEqual(source.load_data()["cases"][0]["body"], "原始")
            self.assertEqual(hashlib.sha256(old_backup.read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
