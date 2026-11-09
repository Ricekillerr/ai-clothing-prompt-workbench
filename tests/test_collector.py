# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

from collector import find_new_or_changed, scan_folder


class CollectorTests(unittest.TestCase):
    def test_scans_supported_files_without_modifying_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "ref.png"
            video = root / "result.mp4"
            ignored = root / "notes.exe"
            hidden = root / ".hidden"
            image.write_bytes(b"png-data")
            video.write_bytes(b"video-data")
            ignored.write_bytes(b"ignored")
            hidden.write_bytes(b"hidden")
            before = {p.name: p.read_bytes() for p in (image, video, ignored, hidden)}
            results = scan_folder(root)
            self.assertEqual({x["name"] for x in results}, {"ref.png", "result.mp4"})
            self.assertEqual({x["media_type"] for x in results}, {"image", "video"})
            self.assertEqual({p.name: p.read_bytes() for p in (image, video, ignored, hidden)}, before)

    def test_detects_new_and_changed_files_by_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = root / "a.txt"
            a.write_text("first", encoding="utf-8")
            first = scan_folder(root)
            snapshot = {x["path"]: x for x in first}
            self.assertEqual(find_new_or_changed(snapshot, first), [])
            a.write_text("second content", encoding="utf-8")
            changed = scan_folder(root)
            delta = find_new_or_changed(snapshot, changed)
            self.assertEqual(len(delta), 1)
            self.assertNotEqual(delta[0]["sha256"], first[0]["sha256"])

    def test_recursive_false_only_scans_top_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "top.txt").write_text("top", encoding="utf-8")
            nested = root / "nested"
            nested.mkdir()
            (nested / "inner.txt").write_text("inner", encoding="utf-8")
            results = scan_folder(root, recursive=False)
            self.assertEqual([x["name"] for x in results], ["top.txt"])

    def test_rejects_missing_or_non_directory_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises((FileNotFoundError, ValueError)):
                scan_folder(Path(tmp) / "missing")
            file_path = Path(tmp) / "a.txt"
            file_path.write_text("a", encoding="utf-8")
            with self.assertRaises(ValueError):
                scan_folder(file_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
