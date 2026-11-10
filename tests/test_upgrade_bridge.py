import ast
import json
import re
import unittest
from pathlib import Path


class UpgradeBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.manifest = json.loads((cls.root / "update_manifest.json").read_text(encoding="utf-8"))
        cls.app = (cls.root / "ai_prompt_workbench.py").read_text(encoding="utf-8")
        cls.updater = (cls.root / "updater.py").read_text(encoding="utf-8")

    def test_bridge_version_and_files_are_accepted_by_v042(self):
        self.assertEqual(self.manifest["version"], "0.4.3")
        legacy_allowed = {
            "ai_prompt_workbench.py", "index.html",
            "启动AI服装提示词工作台.bat", "updater.py",
            "使用说明_AI服装提示词资产工作台_V0.4.2.txt",
        }
        self.assertTrue(set(self.manifest["files"]).issubset(legacy_allowed))

    def test_bridge_expands_both_application_and_updater_allowlists(self):
        self.assertIn('APP_VERSION = "0.4.3"', self.app)
        expected = {'"storage.py"', '"collector.py"', '"case_matcher.py"', '"ai_analyzer.py"'}
        for name in expected:
            self.assertIn(name, self.app)
            self.assertIn(name, self.updater)

    def test_bridge_does_not_import_v050_modules_before_they_exist(self):
        tree = ast.parse(self.app)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertFalse(imported.intersection({"storage", "collector", "case_matcher", "ai_analyzer"}))

    def test_bridge_manifest_hashes_cover_every_downloaded_file(self):
        self.assertEqual(set(self.manifest["files"]), set(self.manifest["sha256"]))
        for name in self.manifest["files"]:
            content = (self.root / name).read_bytes()
            import hashlib
            self.assertEqual(hashlib.sha256(content).hexdigest(), self.manifest["sha256"][name], name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
