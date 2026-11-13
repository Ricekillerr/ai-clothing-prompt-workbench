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
            # An unchanged file reuses the prior digest instead of reading it again.
            self.assertEqual(scan_folder(root, previous=snapshot)[0]["sha256"], first[0]["sha256"])
            a.write_text("second content", encoding="utf-8")
            changed = scan_folder(root)
            delta = find_new_or_changed(snapshot, changed)
            self.assertEqual(len(delta), 1)
            self.assertNotEqual(delta[0]["sha256"], first[0]["sha256"])

    def test_force_hash_detects_same_size_content_change_with_preserved_mtime(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt = root / "prompt.txt"
            prompt.write_text("first", encoding="utf-8")
            first = scan_folder(root)
            snapshot = {item["path"]: item for item in first}
            original_stat = prompt.stat()

            prompt.write_text("other", encoding="utf-8")
            self.assertEqual(prompt.stat().st_size, original_stat.st_size)
            os.utime(
                prompt,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )

            verified = scan_folder(root, previous=snapshot, force_hash=True)
            self.assertNotEqual(verified[0]["sha256"], first[0]["sha256"])
            self.assertEqual(len(find_new_or_changed(snapshot, verified)), 1)

    def test_skips_symlinked_files_and_external_directory_aliases(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            external = Path(outside)
            (external / "secret.png").write_bytes(b"outside")
            (external / "nested").mkdir()
            (external / "nested" / "hidden.txt").write_text("outside", encoding="utf-8")
            try:
                (root / "linked.png").symlink_to(external / "secret.png")
                (root / "linked_dir").symlink_to(external / "nested", target_is_directory=True)
            except OSError:
                self.skipTest("当前系统未授予创建符号链接的权限")
            (root / "safe.txt").write_text("inside", encoding="utf-8")
            results = scan_folder(root)
            self.assertEqual([item["relative_path"] for item in results], ["safe.txt"])
            self.assertTrue(all(Path(item["path"]).is_relative_to(root.resolve()) for item in results))

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
