import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import imageio_ffmpeg
from storage import _attachment_status, Storage, StorageError, KnowledgeStore, create_protected_copy
from collector import scan_folder


class VideoValidationTests(unittest.TestCase):
    def test_import_retry_dedup_and_original_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = Storage(root / "legacy")
            source.save_data({"cases": [{"id": "case1", "body": "原文"}], "modules": []})
            create_protected_copy(source, root / "protected")
            store = Storage(root / "protected" / "data")
            inbox = root / "inbox"
            inbox.mkdir()
            video = inbox / "valid.mp4"
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-f", "lavfi", "-i",
                            "color=c=blue:s=32x32:d=1", "-c:v", "mpeg4", "-y", str(video)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            original = video.read_bytes()
            digest = hashlib.sha256(original).hexdigest()
            source_id = store.register_scan_source(str(inbox))
            store.save_scan_results(source_id, scan_folder(inbox))
            knowledge = KnowledgeStore(store)
            with patch("storage.os.link", side_effect=OSError("injected interruption")):
                with self.assertRaisesRegex(OSError, "injected interruption"):
                    knowledge.import_video("case1", video, digest)
            self.assertEqual(video.read_bytes(), original)
            first = knowledge.import_video("case1", video, digest)
            second = knowledge.import_video("case1", video, digest)
            self.assertEqual(first, second)
            alias = root / "linked-inbox"
            if os.name == "nt":
                command = str(Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe")
                subprocess.run([command, "/c", "mklink", "/J", str(alias), str(inbox)],
                               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                alias.symlink_to(inbox, target_is_directory=True)
            with self.assertRaises(StorageError):
                knowledge.import_video("case1", alias / "valid.mp4", digest)
            self.assertEqual((store.home / "references" / first["path"]).read_bytes(), original)
            with store._session() as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM attachments WHERE asset_id='case1'").fetchone()[0], 1)

    def test_real_video_hash_corruption_and_path_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "valid.mp4"
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-f", "lavfi", "-i",
                            "color=c=red:s=32x32:d=1", "-c:v", "mpeg4", "-y", str(video)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            digest = hashlib.sha256(video.read_bytes()).hexdigest()
            self.assertEqual(_attachment_status(root, "valid.mp4", "video", digest), "verified")
            self.assertEqual(_attachment_status(root, "valid.mp4", "video", "0" * 64), "hash_mismatch")
            self.assertEqual(_attachment_status(root, "../valid.mp4", "video", digest), "unsafe_path")
            broken = root / "broken.mp4"
            broken.write_bytes(video.read_bytes()[:64])
            self.assertEqual(_attachment_status(root, "broken.mp4", "video",
                                                hashlib.sha256(broken.read_bytes()).hexdigest()), "corrupt")
            wrong = root / "wrong.webm"
            wrong.write_bytes(video.read_bytes())
            self.assertEqual(_attachment_status(root, "wrong.webm", "video", digest), "corrupt")
            with patch("storage.MAX_VIDEO_BYTES", 1):
                self.assertEqual(_attachment_status(root, "valid.mp4", "video", digest), "corrupt")


if __name__ == "__main__":
    unittest.main()
