import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_analyzer import analyze_assets


class AnalyzerMultiImageTests(unittest.TestCase):
    def test_analyze_assets_sends_all_selected_images_in_one_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt = root / "look.txt"
            front = root / "front.png"
            back = root / "back.jpg"
            prompt.write_text("黑色无袖立领马甲，拉链完全敞开", encoding="utf-8")
            front.write_bytes(b"front-image-bytes")
            back.write_bytes(b"back-image-bytes")

            result_json = {
                "title": "黑色立领马甲",
                "prompt_text": "黑色无袖立领马甲，拉链完全敞开",
                "category": "服装",
                "risk_notes": [],
                "confidence": 0.9,
            }
            response_payload = {
                "choices": [{
                    "message": {
                        "content": json.dumps(result_json, ensure_ascii=False)
                    }
                }]
            }

            class FakeResponse:
                def __enter__(self):
                    return self

                def __exit__(self, *_):
                    return False

                def read(self, limit=-1):
                    payload = json.dumps(response_payload).encode("utf-8")
                    return payload if limit < 0 else payload[:limit]

            with patch("ai_analyzer.build_opener") as build_opener:
                mocked = build_opener.return_value.open
                mocked.return_value.__enter__.return_value = FakeResponse()
                result = analyze_assets(
                    str(prompt), [str(front), str(back)], "test-key",
                    endpoint="https://example.test/v1", model="test-model"
                )

            request = mocked.call_args.args[0]
            sent = json.loads(request.data.decode("utf-8"))
            user_content = sent["messages"][1]["content"]
            self.assertEqual(sum(item.get("type") == "image_url" for item in user_content), 2)
            self.assertEqual(result["asset_paths"], [str(front.resolve()), str(back.resolve())])
            self.assertEqual(result["asset_path"], str(front.resolve()))
            self.assertEqual(result["model"], "test-model")

    def test_analyze_assets_rejects_more_than_eight_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt = root / "look.txt"
            prompt.write_text("prompt", encoding="utf-8")
            assets = []
            for index in range(9):
                path = root / f"{index}.png"
                path.write_bytes(b"image")
                assets.append(str(path))
            with self.assertRaisesRegex(ValueError, "1–8"):
                analyze_assets(str(prompt), assets, "test-key")


if __name__ == "__main__":
    unittest.main(verbosity=2)
