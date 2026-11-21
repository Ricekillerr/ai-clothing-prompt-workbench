import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import updater
from storage import Storage

ROOT = Path(__file__).resolve().parents[1]
BAT = "启动AI服装提示词工作台.bat"


@unittest.skipUnless(os.name == "nt", "Windows BAT")
class LauncherProbeTests(unittest.TestCase):
    def run_launcher(self, python_works):
        with tempfile.TemporaryDirectory(prefix="中文 路径 ") as temp:
            folder = Path(temp)
            shutil.copy2(ROOT / BAT, folder / BAT)
            system = Path(os.environ["SystemRoot"]) / "System32"
            shutil.copy2(system / "where.exe", folder / "py.exe")
            if not python_works:
                shutil.copy2(system / "where.exe", folder / "python.exe")
            (folder / "ai_prompt_workbench.py").write_text(
                "with open('launches','a') as out: out.write('1')\n", encoding="utf8")
            path = os.pathsep.join(map(str, [folder, system, Path(sys.executable).parent]))
            result = subprocess.run(["cmd.exe", "/d", "/c", BAT], cwd=folder,
                                    env=dict(os.environ, PATH=path), capture_output=True, timeout=15)
            marker = folder / "launches"
            return result.returncode, marker.read_text() if marker.exists() else "", result.stdout

    def test_broken_launcher_falls_back_once(self):
        code, attempts, _ = self.run_launcher(True)
        self.assertEqual((code, attempts), (0, "1"))

    def test_both_interpreters_unavailable(self):
        code, attempts, output = self.run_launcher(False)
        self.assertEqual((code, attempts), (2, ""))
        self.assertIn(b"Python 3", output)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        self.root = base / "program"
        self.root.mkdir()
        self.stage = base / "stage"
        self.stage.mkdir()
        profile = base / "user"
        profile.mkdir()
        self.home = profile / "AI_Prompt_Asset_Workbench"
        self.enterContext(mock.patch.dict(os.environ, {
            "HOME": str(profile), "USERPROFILE": str(profile), "PROMPT_ATELIER_NO_BROWSER": "1",
            "PYTHONDONTWRITEBYTECODE": "1"}))
        for name in json.loads((ROOT / "update_manifest.json").read_text("utf8"))["files"]:
            shutil.copy2(ROOT / name, self.root / name)
        self.store = Storage(self.home)
        self.store.set_setting("sentinel", "original")
        self.image = self.home / "references" / "test.png"
        self.image.parent.mkdir(exist_ok=True)
        self.image.write_bytes(b"original-image")
        self.entry = "ai_prompt_workbench.py"
        (self.root / "installed_version.txt").write_text("0.6.5")
        self.args = ["updater.py", str(self.stage), str(self.root), str(self.root / BAT)]
        self.enterContext(mock.patch.object(sys, "argv", self.args))
        self.children = []
        start = subprocess.Popen
        def track(*args, **kwargs):
            child = start(*args, **kwargs)
            self.children.append(child)
            return child
        self.enterContext(mock.patch.object(updater.subprocess, "Popen", side_effect=track))
        self.addCleanup(self.stop_children)

    def stop_children(self):
        for child in self.children:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=10)

    def prepare(self, files):
        hashes = {}
        for name, content in files.items():
            raw = content.encode()
            (self.stage / name).write_bytes(raw)
            hashes[name] = hashlib.sha256(raw).hexdigest()
        (self.stage / "update_meta.json").write_text(
            json.dumps({"version": "0.6.6", "files": hashes}), encoding="utf8")

    def assert_restored(self):
        self.assertEqual(updater.main(), 9)
        self.assertEqual(Storage(self.home).get_setting("sentinel"), "original")
        self.assertEqual(self.image.read_bytes(), b"original-image")
        self.assertEqual((self.root / "installed_version.txt").read_text(), "0.6.5")
        self.assertFalse((self.root / "_update_transaction.json").exists())
        self.assertIsNone(self.children[-1].poll())

    def test_migration_failure_with_unreadable_database_enters_safe_recovery(self):
        self.prepare({self.entry: "from pathlib import Path\n"
            "home = Path.home() / 'AI_Prompt_Asset_Workbench'\n"
            "(home / 'prompt_library.sqlite3').write_bytes(b'broken migration')\n"
            "(home / 'references/test.png').write_bytes(b'changed')\n"
            "raise RuntimeError('migration failed')\n"})
        self.assertEqual(updater.main(), 9)
        self.assertEqual((self.home / 'prompt_library.sqlite3').read_bytes(), b'broken migration')
        self.assertEqual(self.image.read_bytes(), b'changed')
        state = json.loads((self.root / '_update_transaction.json').read_text('utf8'))
        self.assertTrue(Path(state['data_backup']).is_file())
        self.assertEqual(state['phase'], 'rolling_back')

    def test_launch_error_recovers_old_process(self):
        self.prepare({"index.html": "new"})
        launch = updater.launch_workbench
        def fail_new(root, launcher, version):
            if version == "0.6.6":
                raise OSError("injected launch failure")
            return launch(root, launcher, version)
        with mock.patch.object(updater, "launch_workbench", side_effect=fail_new):
            self.assert_restored()

    def test_timeout_stops_candidate_and_restarts_old_service(self):
        self.prepare({self.entry: "import time\ntime.sleep(60)\n"})
        with mock.patch.object(updater, "HEALTH_TIMEOUT", 4):
            self.assert_restored()
        self.assertIsNotNone(self.children[0].poll())

    def test_new_version_health_commits(self):
        source = (self.root / self.entry).read_text("utf8")
        self.prepare({self.entry: source.replace('APP_VERSION = "0.6.5"', 'APP_VERSION = "0.6.6"')})
        self.assertEqual(updater.main(), 0)
        self.assertFalse((self.root / "_update_transaction.json").exists())
        self.assertEqual((self.root / "installed_version.txt").read_text(), "0.6.6")

    def test_dead_updater_leaves_persistent_safe_state(self):
        self.prepare({"index.html": "candidate"})
        code = ("import os,sys,updater\n"
                "original=updater.replace_with_retry\n"
                "def crash(src,dst):\n original(src,dst)\n os._exit(71)\n"
                "updater.replace_with_retry=crash\nsys.exit(updater.main())")
        result = subprocess.run([sys.executable, "-c", code, *self.args[1:]], cwd=ROOT)
        self.assertEqual(result.returncode, 71)
        state = json.loads((self.root / "_update_transaction.json").read_text("utf8"))
        self.assertEqual(state["phase"], "installing")
        self.assertTrue(Path(state["data_backup"]).is_file())
        self.assertTrue((Path(state["backup"]) / "index.html").is_file())
        normal = subprocess.run([sys.executable, self.entry], cwd=self.root,
                                capture_output=True, timeout=8)
        self.assertNotEqual(normal.returncode, 0)
        self.assertIn(b"_update_transaction.json", normal.stderr)
