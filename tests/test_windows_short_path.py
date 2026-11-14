import ctypes
import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from collector import scan_folder
from storage import Storage


class WindowsShortPathTests(unittest.TestCase):
    def test_ai_source_accepts_equivalent_windows_short_path(self):
        if os.name != "nt":
            self.skipTest("Windows 8.3 short-path regression requires Windows")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            store = Storage(home)
            source_dir = home / "history"
            source_dir.mkdir()
            prompt = source_dir / "prompt_with_long_name.txt"
            prompt.write_bytes(b"original prompt")
            source_id = store.register_scan_source(str(source_dir))
            store.save_scan_results(source_id, scan_folder(source_dir))

            buffer = ctypes.create_unicode_buffer(32768)
            length = ctypes.windll.kernel32.GetShortPathNameW(
                str(prompt), buffer, len(buffer)
            )
            if not length or length >= len(buffer):
                self.skipTest("Windows did not provide an 8.3 short path")
            short_path = buffer.value
            if os.path.normcase(short_path) == os.path.normcase(str(prompt)):
                self.skipTest("8.3 short paths are disabled for this volume")

            expected_hash = hashlib.sha256(prompt.read_bytes()).hexdigest()
            with store._session() as con:
                verified = store._verified_ingest_source_path(
                    con, short_path, "text", expected_hash
                )
            self.assertEqual(verified, prompt.resolve())


if __name__ == "__main__":
    unittest.main()
