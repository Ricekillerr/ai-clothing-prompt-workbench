import errno
import importlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock


class DuplicateInstanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_userprofile = os.environ.get("USERPROFILE")
        self.old_home = os.environ.get("HOME")
        os.environ["USERPROFILE"] = self.temp.name
        os.environ["HOME"] = self.temp.name
        sys.modules.pop("ai_prompt_workbench", None)
        self.app = importlib.import_module("ai_prompt_workbench")

    def tearDown(self):
        sys.modules.pop("ai_prompt_workbench", None)
        if self.old_userprofile is None:
            os.environ.pop("USERPROFILE", None)
        else:
            os.environ["USERPROFILE"] = self.old_userprofile
        if self.old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.old_home
        self.temp.cleanup()

    def test_second_server_cannot_bind_the_same_loopback_port(self):
        first = self.app.WorkbenchHTTPServer(("127.0.0.1", 0), self.app.Handler)
        try:
            port = first.server_address[1]
            with self.assertRaises(OSError) as caught:
                self.app.WorkbenchHTTPServer(("127.0.0.1", port), self.app.Handler)
            error = caught.exception
            self.assertTrue(
                getattr(error, "winerror", None) == 10048
                or getattr(error, "errno", None) == errno.EADDRINUSE,
                repr(error),
            )
        finally:
            first.server_close()

    def test_main_reports_port_conflict_without_starting_background_threads(self):
        output = io.StringIO()
        occupied = OSError(errno.EADDRINUSE, "address in use")
        with mock.patch.object(self.app, "ThreadingHTTPServer", side_effect=occupied), \
             mock.patch.object(self.app.threading, "Thread") as thread, \
             mock.patch.object(self.app.threading, "Timer") as timer, \
             mock.patch.object(self.app.sys, "stderr", output):
            result = self.app.main()
        self.assertEqual(result, 2)
        self.assertIn("端口 8765 已被占用", output.getvalue())
        thread.assert_not_called()
        timer.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
