import tempfile
import unittest
from pathlib import Path

from ai_analyzer import _safe_endpoint, analyze_pair


class AIAnalyzerTests(unittest.TestCase):
    def test_endpoint_normalizes_base_url(self):
        self.assertEqual(_safe_endpoint("https://api.example.com/v1"),
                         "https://api.example.com/v1/chat/completions")

    def test_endpoint_rejects_embedded_credentials(self):
        with self.assertRaises(ValueError):
            _safe_endpoint("https://user:pass@example.com/v1")

    def test_api_key_is_required_before_network_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompt = Path(tmp) / "prompt.txt"
            image = Path(tmp) / "image.png"
            prompt.write_text("prompt", encoding="utf-8")
            image.write_bytes(b"image")
            with self.assertRaisesRegex(ValueError, "API Key"):
                analyze_pair(str(prompt), str(image), "")

    def test_non_prompt_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompt = Path(tmp) / "prompt.exe"
            image = Path(tmp) / "image.png"
            prompt.write_text("x", encoding="utf-8")
            image.write_bytes(b"x")
            with self.assertRaisesRegex(ValueError, "TXT、MD、JSON"):
                analyze_pair(str(prompt), str(image), "test-key")


if __name__ == "__main__":
    unittest.main(verbosity=2)
