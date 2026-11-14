import importlib
import os
import sys
import tempfile
import unittest
from io import BytesIO, TextIOWrapper
from pathlib import Path
from unittest import mock


class ConsoleOutputTests(unittest.TestCase):
    def test_main_supports_non_utf8_windows_locale(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_userprofile = os.environ.get("USERPROFILE")
            old_home = os.environ.get("HOME")
            os.environ["USERPROFILE"] = tmp
            os.environ["HOME"] = tmp
            sys.modules.pop("ai_prompt_workbench", None)
            try:
                app = importlib.import_module("ai_prompt_workbench")
                home = Path(tmp) / "PromptAtelierConsoleSmoke"
                app.HOME = home
                app.IMAGES = home / "references"
                app.STORAGE = app.Storage(home, legacy_json=home / "prompt_library.json")

                class FakeServer:
                    def serve_forever(self):
                        pass

                    def server_close(self):
                        pass

                output = TextIOWrapper(BytesIO(), encoding="ascii", errors="strict")
                with mock.patch.object(app, "ThreadingHTTPServer", return_value=FakeServer()), \
                     mock.patch.object(app.threading, "Thread"), \
                     mock.patch.object(app.threading, "Timer"), \
                     mock.patch.object(app, "auto_scan_is_enabled", return_value=False), \
                     mock.patch.object(app.sys, "stdout", output), \
                     mock.patch.dict(os.environ, {"PROMPT_ATELIER_NO_BROWSER": "1"}):
                    app.main()
                output.flush()
                self.assertIn("Prompt Atelier", output.buffer.getvalue().decode("utf-8"))
            finally:
                sys.modules.pop("ai_prompt_workbench", None)
                if old_userprofile is None:
                    os.environ.pop("USERPROFILE", None)
                else:
                    os.environ["USERPROFILE"] = old_userprofile
                if old_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_home


if __name__ == "__main__":
    unittest.main(verbosity=2)
