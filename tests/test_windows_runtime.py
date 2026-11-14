import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "启动AI服装提示词工作台.bat"


def _windows_runtime_ready():
    if os.name != "nt":
        return False
    if not shutil.which("cmd.exe"):
        return False
    if not LAUNCHER.is_file():
        return False
    return True


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(_windows_runtime_ready(), "requires Windows cmd.exe and the release launcher")
class WindowsRuntimeIntegrationTests(unittest.TestCase):
    """Small opt-in smoke test for the shipped launcher and updater transaction.

    It uses an isolated temporary HOME and a copied release tree. It never installs
    packages, changes Git settings, touches a user's real data, or uses update.bat.
    """

    def test_launcher_starts_local_service_and_serves_version(self):
        with tempfile.TemporaryDirectory(prefix="prompt_atelier_windows_test_") as tmp:
            root = Path(tmp)
            for name in ("ai_prompt_workbench.py", "storage.py", "collector.py",
                         "case_matcher.py", "ai_analyzer.py", "updater.py", "index.html",
                         LAUNCHER.name):
                shutil.copy2(ROOT / name, root / name)
            env = os.environ.copy()
            env["HOME"] = str(root / "isolated-home")
            env["USERPROFILE"] = env["HOME"]
            env["PROMPT_ATELIER_NO_BROWSER"] = "1"
            for key in ("OPENAI_API_KEY", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
                env.pop(key, None)
            log_path = root / "launcher.log"
            proc = None
            log = None
            try:
                log = log_path.open("w", encoding="utf-8")
                proc = subprocess.Popen(
                    ["cmd.exe", "/c", str(root / LAUNCHER.name)],
                    cwd=str(root), env=env,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                )
                base = "http://127.0.0.1:8765"
                deadline = time.time() + 20
                response = None
                while time.time() < deadline:
                    try:
                        with urllib.request.urlopen(base + "/api/version", timeout=1) as result:
                            response = json.loads(result.read().decode("utf-8"))
                            break
                    except Exception:
                        if proc.poll() is not None:
                            break
                        time.sleep(0.25)
                self.assertIsNotNone(response, "BAT launcher did not expose /api/version")
                self.assertEqual(response.get("version"), "0.6.5")
                payload = json.dumps({"enabled": True}).encode("utf-8")
                request = urllib.request.Request(
                    base + "/api/settings/auto-update", data=payload,
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as result:
                    self.assertEqual(json.loads(result.read().decode("utf-8"))["enabled"], True)
                close_request = urllib.request.Request(
                    base + "/api/client/close", data=b"{}",
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(close_request, timeout=5) as result:
                    self.assertTrue(json.loads(result.read().decode("utf-8"))["ok"])
                proc.wait(timeout=20)
            finally:
                if proc is not None and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
                if log is not None:
                    log.close()

    def test_headless_browser_flag_preserves_default_browser_behavior(self):
        import ai_prompt_workbench as app
        from unittest import mock

        class Timer:
            created = []
            def __init__(self, delay, callback):
                self.delay = delay
                self.callback = callback
                self.started = False
                self.__class__.created.append(self)
            def start(self):
                self.started = True

        class Server:
            def __init__(self, address, handler):
                self.address = address
            def serve_forever(self):
                raise KeyboardInterrupt
            def server_close(self):
                pass

        for value, expected in (("1", False), (None, True)):
            Timer.created.clear()
            with mock.patch.dict(os.environ, {"PROMPT_ATELIER_NO_BROWSER": value} if value else {}, clear=False):
                if value is None:
                    os.environ.pop("PROMPT_ATELIER_NO_BROWSER", None)
                with mock.patch.object(app, "ThreadingHTTPServer", Server), \
                     mock.patch.object(app.threading, "Timer", Timer), \
                     mock.patch.object(app.threading, "Thread", return_value=mock.Mock(start=mock.Mock())), \
                     mock.patch.object(app, "AUTO_SCAN_STOP") as stop:
                    stop.wait.side_effect = KeyboardInterrupt
                    try:
                        app.main()
                    except KeyboardInterrupt:
                        pass
            self.assertEqual(bool(Timer.created), expected)
            if expected:
                self.assertTrue(Timer.created[0].started)

    def test_launcher_file_is_utf8_crlf_and_not_text_normalized(self):
        content = LAUNCHER.read_bytes()
        self.assertFalse(content.startswith(b"\xef\xbb\xbf"))
        self.assertGreater(content.count(b"\r\n"), 20)
        self.assertNotIn(b"\n", content.replace(b"\r\n", b""))
        self.assertEqual(content.decode("utf-8").encode("utf-8"), content)


    def test_manifest_generator_preserves_bat_hash_and_existing_metadata(self):
        import hashlib
        import importlib.util
        from unittest import mock

        with tempfile.TemporaryDirectory(prefix="prompt_atelier_manifest_test_") as tmp:
            root = Path(tmp)
            source = ROOT / "生成更新清单.py"
            generator = root / source.name
            shutil.copy2(source, generator)
            spec = importlib.util.spec_from_file_location("manifest_generator_windows_test", generator)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            for name in module.RELEASE_FILES:
                shutil.copy2(ROOT / name, root / name)
            (root / "ai_prompt_workbench.py").write_text(
                'APP_VERSION = "9.8.7"\n', encoding="utf-8"
            )
            existing = json.loads((ROOT / "update_manifest.json").read_text(encoding="utf-8"))
            existing["notes"] = "keep these release notes"
            existing["sha256"] = dict(reversed(list(existing["sha256"].items())))
            (root / "update_manifest.json").write_text(
                json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            module.ROOT = root
            module.MAIN = root / "ai_prompt_workbench.py"
            module.main()
            generated = json.loads((root / "update_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(generated["notes"], "keep these release notes")
            self.assertEqual(list(generated["sha256"]), list(existing["sha256"]))
            self.assertEqual(
                generated["sha256"][LAUNCHER.name],
                hashlib.sha256(LAUNCHER.read_bytes()).hexdigest(),
            )
            first = (root / "update_manifest.json").read_bytes()
            module.main()
            self.assertEqual((root / "update_manifest.json").read_bytes(), first)

    def test_updater_applies_update_and_rolls_back_on_file_failure(self):
        with tempfile.TemporaryDirectory(prefix="prompt_atelier_updater_test_") as tmp:
            root = Path(tmp)
            install = root / "install"
            stage = root / "stage"
            install.mkdir()
            stage.mkdir()
            env = os.environ.copy()
            env["HOME"] = str(root / "isolated-home")
            env["USERPROFILE"] = env["HOME"]
            protected = ["prompt_library.json", "updater.pid", "updater.log",
                         "updater_state.json", "updater.log.1", "updater.log.2",
                         "updater.log.3", "updater.log.4", "updater.log.5",
                         "updater.log.6", "updater.log.7", "updater.log.8", "updater.log.9"]
            for name in ("ai_prompt_workbench.py", "storage.py", "index.html", "updater.py", LAUNCHER.name):
                (install / name).write_text("old-" + name, encoding="utf-8")
            for name in protected:
                (install / name).write_text("keep-" + name, encoding="utf-8")
            names = ["ai_prompt_workbench.py", "storage.py", "index.html", "updater.py", LAUNCHER.name]
            meta = {"version": "0.6.5", "files": {name: "0" * 64 for name in names}}
            for name in names:
                (stage / name).write_text("new-" + name, encoding="utf-8")
            (stage / "update_meta.json").write_text(json.dumps(meta), encoding="utf-8")
            before = {name: (install / name).read_text(encoding="utf-8") for name in names}
            result = subprocess.run(
                [sys.executable, str(stage / "updater.py"), str(stage), str(install),
                 str(install / LAUNCHER.name)],
                cwd=str(install), env=env, capture_output=True, text=True,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for name in names:
                self.assertEqual((install / name).read_text(encoding="utf-8"), "new-" + name)
            for name in protected:
                self.assertEqual((install / name).read_text(encoding="utf-8"), "keep-" + name)
            self.assertTrue((install / "updater_state.json").is_file())
            state = json.loads((install / "updater_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["phase"], "complete")
            self.assertEqual(state["version"], "0.6.5")

            for name in names:
                (stage / name).write_text("second-" + name, encoding="utf-8")
            original_replace = os.replace
            def fail_second(source, target):
                if Path(target).name == "storage.py" and Path(source).name == "storage.py":
                    raise OSError("simulated update failure")
                return original_replace(source, target)
            import updater
            with unittest.mock.patch("updater.os.replace", side_effect=fail_second):
                with self.assertRaises(OSError):
                    updater.main([str(stage), str(install), str(install / LAUNCHER.name)])
            for name in names:
                self.assertEqual((install / name).read_text(encoding="utf-8"), "new-" + name)
            for name in protected:
                self.assertEqual((install / name).read_text(encoding="utf-8"), "keep-" + name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
