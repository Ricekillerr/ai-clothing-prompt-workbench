import hashlib
import importlib
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest import mock
from urllib.request import Request, urlopen


class WindowsUpdaterTransactionTests(unittest.TestCase):
    def _stage(self, root, files, version="0.6.6"):
        stage = root / "stage"
        stage.mkdir()
        expected = {}
        for name, value in files.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")
            expected[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        (stage / "update_meta.json").write_text(
            json.dumps({"version": version, "files": expected}), encoding="utf-8"
        )
        return stage

    def test_update_installs_verified_files_and_retains_backup(self):
        import updater

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "install"
            root.mkdir()
            (root / "index.html").write_text("old-ui", encoding="utf-8")
            (root / "storage.py").write_text("old-storage", encoding="utf-8")
            launcher = root / "launcher.bat"
            launcher.write_text("@echo off", encoding="utf-8")
            stage = self._stage(root, {
                "index.html": "new-ui",
                "storage.py": "new-storage",
            })
            argv = ["updater.py", str(stage), str(root), str(launcher)]
            with mock.patch.object(sys, "argv", argv), \
                 mock.patch.object(updater, "service_running", return_value=False), \
                 mock.patch.object(updater, "launch_workbench") as launch:
                result = updater.main()
            self.assertEqual(result, 0)
            self.assertEqual((root / "index.html").read_text(encoding="utf-8"), "new-ui")
            self.assertEqual((root / "storage.py").read_text(encoding="utf-8"), "new-storage")
            self.assertEqual((root / "installed_version.txt").read_text(encoding="utf-8"), "0.6.6")
            self.assertEqual((root / "_update_backup" / "index.html").read_text(encoding="utf-8"), "old-ui")
            launch.assert_called_once()
            self.assertFalse(stage.exists())

    def test_mid_transaction_failure_restores_every_original_file(self):
        import updater

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "install"
            root.mkdir()
            originals = {"index.html": "old-ui", "storage.py": "old-storage"}
            for name, value in originals.items():
                (root / name).write_text(value, encoding="utf-8")
            launcher = root / "launcher.bat"
            launcher.write_text("@echo off", encoding="utf-8")
            stage = self._stage(root, {
                "index.html": "new-ui",
                "storage.py": "new-storage",
            })
            real_replace = updater.replace_with_retry
            calls = {"count": 0}

            def fail_once(src, dst):
                calls["count"] += 1
                if calls["count"] == 2:
                    raise OSError("injected replacement failure")
                return real_replace(src, dst)

            argv = ["updater.py", str(stage), str(root), str(launcher)]
            with mock.patch.object(sys, "argv", argv), \
                 mock.patch.object(updater, "service_running", return_value=False), \
                 mock.patch.object(updater, "launch_workbench") as launch, \
                 mock.patch.object(updater, "replace_with_retry", side_effect=fail_once):
                result = updater.main()
            self.assertEqual(result, 9)
            for name, value in originals.items():
                self.assertEqual((root / name).read_text(encoding="utf-8"), value)
            self.assertFalse((root / "installed_version.txt").exists())
            self.assertTrue((root / "_update_error.log").is_file())
            launch.assert_not_called()
            self.assertFalse(stage.exists())


class WindowsLocalServerSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.old_userprofile = os.environ.get("USERPROFILE")
        cls.old_home = os.environ.get("HOME")
        os.environ["USERPROFILE"] = cls.temp.name
        os.environ["HOME"] = cls.temp.name
        sys.modules.pop("ai_prompt_workbench", None)
        cls.app = importlib.import_module("ai_prompt_workbench")
        cls.home = Path(cls.temp.name) / "PromptAtelierWindowsSmoke"
        cls.images = cls.home / "references"
        cls.images.mkdir(parents=True, exist_ok=True)
        cls.app.HOME = cls.home
        cls.app.IMAGES = cls.images
        cls.app.STORAGE = cls.app.Storage(cls.home, legacy_json=cls.home / "prompt_library.json")

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("ai_prompt_workbench", None)
        if cls.old_userprofile is None:
            os.environ.pop("USERPROFILE", None)
        else:
            os.environ["USERPROFILE"] = cls.old_userprofile
        if cls.old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = cls.old_home
        cls.temp.cleanup()

    def _start_server(self):
        server = self.app.ThreadingHTTPServer(("127.0.0.1", 0), self.app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread, f"http://127.0.0.1:{server.server_address[1]}"

    def test_local_api_backup_and_restart_preserve_user_data(self):
        server, thread, base = self._start_server()
        try:
            with urlopen(base + "/api/version", timeout=10) as response:
                version = json.loads(response.read().decode("utf-8"))
            self.assertEqual(version["version"], self.app.APP_VERSION)

            payload = {
                "cases": [{"id": "windows-smoke-case", "name": "Windows smoke",
                           "body": "verified prompt", "status": "已验证成功"}],
                "modules": [],
            }
            request = Request(
                base + "/api/data",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=10) as response:
                self.assertTrue(json.loads(response.read().decode("utf-8"))["ok"])

            (self.images / "smoke-reference.png").write_bytes(b"smoke-reference-bytes")
            with urlopen(base + "/api/backup/full", timeout=20) as response:
                archive_bytes = response.read()
            with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
                names = set(archive.namelist())
                self.assertIn("database/prompt_library.sqlite3", names)
                self.assertIn("data/prompt_library.json", names)
                self.assertIn("references/smoke-reference.png", names)
                self.assertIn("manifest.json", names)
                self.assertTrue(archive.testzip() is None)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        restarted, restarted_thread, restarted_base = self._start_server()
        try:
            with urlopen(restarted_base + "/api/data", timeout=10) as response:
                restored = json.loads(response.read().decode("utf-8"))
            self.assertEqual(restored["cases"][0]["id"], "windows-smoke-case")
            self.assertEqual(restored["cases"][0]["body"], "verified prompt")
            self.assertTrue((self.images / "smoke-reference.png").is_file())
        finally:
            restarted.shutdown()
            restarted.server_close()
            restarted_thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
