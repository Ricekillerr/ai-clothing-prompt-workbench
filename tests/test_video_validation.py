import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import imageio_ffmpeg
from storage import _attachment_status


class VideoValidationTests(unittest.TestCase):
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
