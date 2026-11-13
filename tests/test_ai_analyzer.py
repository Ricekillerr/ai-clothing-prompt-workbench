import tempfile
import unittest
from io import BytesIO
from urllib.request import Request
from pathlib import Path

from ai_analyzer import (_NoRedirectHandler, MAX_RESPONSE_BYTES, _read_api_response, _read_text, _safe_endpoint, analyze_pair, normalize_asset_roles, normalize_outfit_items)


class AIAnalyzerTests(unittest.TestCase):
    def test_endpoint_normalizes_base_url(self):
        self.assertEqual(_safe_endpoint("https://api.example.com/v1"),
                         "https://api.example.com/v1/chat/completions")

    def test_endpoint_allows_plain_http_only_for_loopback(self):
        self.assertEqual(_safe_endpoint("http://127.0.0.1:8080/v1"),
                         "http://127.0.0.1:8080/v1/chat/completions")
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            _safe_endpoint("http://api.example.com/v1")

    def test_api_key_is_not_forwarded_to_redirect_targets(self):
        handler = _NoRedirectHandler()
        request = Request("https://trusted.example/v1/chat/completions")
        with self.assertRaisesRegex(ValueError, "阻止转发 API Key"):
            handler.redirect_request(
                request, None, 302, "Found", {}, "https://untrusted.example/steal"
            )

    def test_ai_response_size_is_bounded(self):
        self.assertEqual(_read_api_response(BytesIO(b'{"ok":true}')), {"ok": True})
        with self.assertRaisesRegex(ValueError, "超过 4MB"):
            _read_api_response(BytesIO(b"x" * (MAX_RESPONSE_BYTES + 1)))

    def test_prompt_file_size_is_checked_before_full_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompt = Path(tmp) / "oversized.txt"
            prompt.write_bytes(b"x" * (1024 * 1024 + 1))
            with self.assertRaisesRegex(ValueError, "超过 1MB"):
                _read_text(prompt)

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

    def test_image_roles_are_ordered_and_mapped_to_exact_images(self):
        images = [Path("front.png"), Path("back.jpg"), Path("detail.webp")]
        roles = [
            {"asset_index": 3, "role": "detail", "role_label": "细节图"},
            {"asset_index": 1, "role": "front", "role_label": "正面图",
             "priority": "primary", "governs": ["结构", "版型"]},
            {"asset_index": 2, "role": "back", "role_label": "背面图"},
        ]
        result = normalize_asset_roles(roles, images)
        self.assertEqual([item["asset_path"] for item in result],
                         ["front.png", "back.jpg", "detail.webp"])
        self.assertEqual([item["role"] for item in result],
                         ["front", "back", "detail"])
        self.assertEqual(result[0]["priority"], "primary")
        self.assertEqual(result[0]["governs"], ["结构", "版型"])
        self.assertEqual(result[1]["priority"], "primary")
        self.assertEqual(result[2]["priority"], "secondary")

    def test_missing_invalid_and_out_of_range_roles_fall_back_to_unknown(self):
        images = [Path("one.png"), Path("two.png")]
        roles = [
            {"asset_index": 1, "role": "invented-role", "role_label": "奇怪分类"},
            {"asset_index": 9, "role": "back"},
            {"asset_index": "bad", "role": "front"},
        ]
        result = normalize_asset_roles(roles, images)
        self.assertEqual([item["role"] for item in result], ["unknown", "unknown"])
        self.assertEqual(result[0]["role_label"], "奇怪分类")
        self.assertEqual(result[1]["role_label"], "待人工确认")

    def test_duplicate_role_indices_do_not_override_first_valid_role(self):
        result = normalize_asset_roles([
            {"asset_index": 1, "role": "front"},
            {"asset_index": 1, "role": "back"},
        ], [Path("front.png")])
        self.assertEqual(result[0]["role"], "front")

    def test_outfit_items_are_bounded_and_keep_structured_attributes(self):
        items, relations = normalize_outfit_items([
            {"category": "上装", "name": "黑色马甲", "material": "尼龙",
             "details": ["立领", "双拉头"], "confidence": 0.91},
            {"category": "奇怪分类", "name": "鞋", "confidence": 2},
        ], [
            {"source_item": 1, "target_item": 2, "relation": "over",
             "evidence": "图片中可见外套覆盖内搭"},
            {"source_item": 1, "target_item": 99, "relation": "over"},
        ])
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["category"], "上装")
        self.assertEqual(items[0]["details"], ["立领", "双拉头"])
        self.assertEqual(items[0]["confidence"], 0.91)
        self.assertEqual(items[1]["category"], "其他")
        self.assertEqual(items[1]["confidence"], 1.0)
        self.assertEqual(len(relations), 1)
        self.assertEqual(relations[0]["relation"], "over")

    def test_outfit_item_normalizer_ignores_invalid_and_caps_item_count(self):
        raw = [{"category": "下装", "name": f"裤子{i}"} for i in range(15)]
        raw.insert(1, "not-an-object")
        items, relations = normalize_outfit_items(raw, [
            {"source_item": "bad", "target_item": 1, "relation": "over"},
            {"source_item": 1, "target_item": 1, "relation": "under"},
        ])
        self.assertEqual(len(items), 12)
        self.assertEqual(relations, [])

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
