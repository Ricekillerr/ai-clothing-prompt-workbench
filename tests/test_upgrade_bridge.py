import ast
import hashlib
import json
import unittest
from pathlib import Path


class V052IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.manifest = json.loads((cls.root / "update_manifest.json").read_text(encoding="utf-8"))
        cls.app = (cls.root / "ai_prompt_workbench.py").read_text(encoding="utf-8")
        cls.updater = (cls.root / "updater.py").read_text(encoding="utf-8")

    def test_manifest_is_v052_and_all_release_files_are_hash_covered(self):
        self.assertEqual(self.manifest["version"], "0.5.2")
        self.assertEqual(set(self.manifest["files"]), set(self.manifest["sha256"]))
        for name in self.manifest["files"]:
            content = (self.root / name).read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest(), self.manifest["sha256"][name], name)

    def test_v043_client_and_updater_accept_all_v052_release_files(self):
        tree = ast.parse(self.app)
        app_allow = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "ALLOWED_UPDATE_FILES":
                        app_allow.update(ast.literal_eval(node.value))
        updater_tree = ast.parse(self.updater)
        updater_allow = set()
        for node in ast.walk(updater_tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "ALLOWED":
                        updater_allow.update(ast.literal_eval(node.value))
        self.assertTrue(set(self.manifest["files"]).issubset(app_allow))
        self.assertTrue(set(self.manifest["files"]).issubset(updater_allow))

    def test_backend_connects_storage_and_exposes_ingestion_routes(self):
        self.assertIn("STORAGE = Storage(", self.app)
        self.assertIn("from collector import scan_folder, sha256_file", self.app)
        for route in ("/api/scan/sources", "/api/scan/pending", "/api/scan/matches",
                      "/api/scan/source", "/api/scan/run", "/api/ai/analyze",
                      "/api/ai/approve", "/api/ai/reject", "/api/cases/search"):
            self.assertIn(route, self.app)

    def test_frontend_contains_scanner_review_and_ai_controls(self):
        html = (self.root / "index.html").read_text(encoding="utf-8")
        for text in ("历史案例自动采集", "立即扫描", "匹配提示词与素材",
                     "AI审核结果", "确认并入库", "智能案例检索", "匹配度达到 80%", "async function scanApi"):
            self.assertIn(text, html)
        self.assertIn("renderMatchAttributeDetails", html)
        self.assertIn("matched_terms", (self.root / "case_matcher.py").read_text(encoding="utf-8"))
        self.assertIn("includeConflicts", html)
        self.assertIn("renderMatchConflicts", html)
        self.assertIn("include_conflicts", self.app)
        self.assertIn("threshold_raw", self.app)
        self.assertIn("findComposeConflicts", html)
        self.assertIn("composeWarnings", html)
        self.assertIn("发现组合冲突，已阻止生成", html)
        self.assertIn("composition:", html)
        self.assertIn("base_case_id", html)
        self.assertIn("module_ids", html)
        self.assertIn("组合来源记录", html)

    def test_legacy_json_is_migrated_without_replacing_source(self):
        self.assertIn("legacy_json=LEGACY_DATA", self.app)
        self.assertIn("STORAGE.load_data()", self.app)
        self.assertIn("STORAGE.save_data(d)", self.app)


if __name__ == "__main__":
    unittest.main(verbosity=2)
