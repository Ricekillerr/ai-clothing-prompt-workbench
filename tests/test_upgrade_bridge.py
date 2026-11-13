import ast
import hashlib
import json
import unittest
from pathlib import Path


class V065IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.manifest = json.loads((cls.root / "update_manifest.json").read_text(encoding="utf-8"))
        cls.app = (cls.root / "ai_prompt_workbench.py").read_text(encoding="utf-8")
        cls.updater = (cls.root / "updater.py").read_text(encoding="utf-8")

    def test_manifest_is_v065_and_all_release_files_are_hash_covered(self):
        self.assertEqual(self.manifest["version"], "0.6.5")
        self.assertEqual(set(self.manifest["files"]), set(self.manifest["sha256"]))
        for name in self.manifest["files"]:
            content = (self.root / name).read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest(), self.manifest["sha256"][name], name)

    def test_client_and_updater_accept_all_v065_release_files(self):
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
        self.assertIn("create_full_backup", self.app)
        self.assertIn("pre_update_backup = STORAGE.create_full_backup", self.app)
        self.assertIn("analyze_assets", self.app)
        self.assertIn("AUTO_SCAN_INTERVAL_SECONDS = 60", self.app)
        self.assertIn("def auto_scan_loop", self.app)
        self.assertIn("def heartbeat_watchdog", self.app)
        self.assertIn("list_asset_revisions", self.app)
        self.assertIn("restore_asset_revision", self.app)
        self.assertIn("from collector import scan_folder, sha256_file", self.app)
        for route in ("/api/scan/sources", "/api/scan/pending", "/api/scan/matches",
                      "/api/scan/source", "/api/scan/run", "/api/ai/analyze",
                      "/api/ai/approve", "/api/ai/reject", "/api/cases/search",
                      "/api/backup/full", "/api/scan/auto",
                      "/api/client/heartbeat", "/api/client/close",
                      "/api/assets/revisions", "/api/assets/revisions/restore"):
            self.assertIn(route, self.app)

    def test_frontend_contains_scanner_review_and_ai_controls(self):
        html = (self.root / "index.html").read_text(encoding="utf-8")
        for text in ("历史案例自动采集", "立即扫描", "匹配提示词与素材",
                     "AI审核结果", "确认并入库", "智能案例检索", "匹配度达到 80%", "async function scanApi"):
            self.assertIn(text, html)
        self.assertIn("renderMatchAttributeDetails", html)
        self.assertIn("saveQueue", html)
        self.assertIn("reconcileSaveFailure", html)
        self.assertIn("uncertain", html)
        self.assertIn("saveFailureMessage", html)
        self.assertIn("保存响应超时", html)
        self.assertIn("AbortController", html)
        self.assertIn("请求超时", html)
        self.assertIn("log_api_error", self.app)
        self.assertIn("api_errors.log", self.app)
        diagnostics = self.app[self.app.index("def log_api_error"):self.app.index("def auto_scan_loop")]
        self.assertNotIn("str(error)", diagnostics)
        self.assertIn("saveQueue", html)
        self.assertIn("匹配有帮助", html)
        self.assertIn("不符合需求", html)
        self.assertIn("recordCaseFeedback", html)
        self.assertIn("loadRetrievalAnalytics", html)
        self.assertIn("retrievalAnalytics", html)
        self.assertIn("/api/cases/feedback", self.app)
        self.assertIn("/api/analytics/retrieval", self.app)
        self.assertIn("retrieval_feedback", (self.root / "storage.py").read_text(encoding="utf-8"))
        self.assertIn("matched_terms", (self.root / "case_matcher.py").read_text(encoding="utf-8"))
        self.assertIn("includeConflicts", html)
        self.assertIn("renderMatchConflicts", html)
        smart_search = html[html.index("async function runSmartCaseSearch()"):html.index("\nfunction gallery()")]
        self.assertNotIn(chr(92)+"${", smart_search)
        self.assertIn("[[\"商品款号\",x.sku||x.style_number||x.product_code]", html)
        for field in ("silhouette","fit","fabric","texture","details","craftsmanship","constraints"):
            self.assertIn(f'["{field}",', html)
        self.assertIn('["廓形",x.silhouette]', html)
        self.assertIn('["面料组成",x.fabric]', html)
        self.assertIn("include_conflicts", self.app)
        self.assertIn("threshold_raw", self.app)
        self.assertIn("downloadFullBackup", html)
        self.assertIn("showAssetHistory", html)
        self.assertIn("compareAssetRevision", html)
        self.assertIn("对比提示词", html)
        self.assertIn("/api/assets/revision", (self.root / "ai_prompt_workbench.py").read_text(encoding="utf-8"))
        self.assertIn("get_asset_revision", (self.root / "storage.py").read_text(encoding="utf-8"))
        self.assertIn("restoreAssetRevision", html)
        self.assertIn("autoScanToggle", html)
        self.assertIn("refreshAutoScanState", html)
        self.assertIn("asset_paths:selected", html)
        self.assertIn("async function runAiAnalysis", html)
        self.assertIn("asset_roles", (self.root / "ai_analyzer.py").read_text(encoding="utf-8"))
        self.assertIn("normalize_asset_roles", (self.root / "ai_analyzer.py").read_text(encoding="utf-8"))
        self.assertIn("参考图片自动分类", html)
        self.assertIn("图片分类：", html)
        self.assertIn("主参考", html)
        self.assertIn("supporting", (self.root / "ai_analyzer.py").read_text(encoding="utf-8"))
        self.assertIn('"priority"', (self.root / "storage.py").read_text(encoding="utf-8"))
        import json
        manifest = json.loads((self.root / "update_manifest.json").read_text(encoding="utf-8"))
        self.assertIn("collector.py", manifest["files"])
        self.assertIn("case_matcher.py", manifest["files"])
        self.assertIn("collector.py", manifest["sha256"])
        self.assertIn("case_matcher.py", manifest["sha256"])
        self.assertIn("整套穿搭拆解（AI 识别，需人工核实）", html)
        self.assertIn("function parseOutfitItems", html)
        self.assertIn("function parseOutfitRelations", html)
        self.assertIn("function parseImageRoles", html)
        self.assertIn('data-open-case="${esc(x.id)}"', html)
        self.assertIn('data-open-module="${esc(x.id)}"', html)
        self.assertNotIn('onclick="openDetail(\'${x.id}\'', html)
        self.assertNotIn('onclick="copyText(${JSON.stringify(x.body', html)
        self.assertIn("const previous=JSON.parse(JSON.stringify(db))", html[html.index("function importJSON"):html.index("async function uploadImage")])
        self.assertIn("db=previous", html[html.index("function importJSON"):html.index("async function uploadImage")])
        self.assertIn("参考图优先级", html)
        self.assertIn("primary", html)
        self.assertIn("f_image_roles", html)
        self.assertIn("function imagePerceptualHash", html)
        self.assertIn("function runVisualCaseSearch", html)
        self.assertIn("选择图片找相似案例", html)
        self.assertIn("不上传图片", html)
        self.assertIn("f_outfit_items", html)
        self.assertIn("f_outfit_relations", html)
        self.assertIn("outfit_items", (self.root / "storage.py").read_text(encoding="utf-8"))
        self.assertIn("outfit_relations", (self.root / "storage.py").read_text(encoding="utf-8"))
        self.assertIn("outfit_items", (self.root / "case_matcher.py").read_text(encoding="utf-8"))
        self.assertIn('"image_roles"', (self.root / "storage.py").read_text(encoding="utf-8"))
        self.assertIn("startWorkbenchHeartbeat", html)
        self.assertIn("完整 ZIP 备份", html)
        self.assertIn("SHA-256", html)
        self.assertIn("findComposeConflicts", html)
        self.assertIn('"runninghub"', html)
        self.assertIn('"Seedance 2.5"', html)
        self.assertIn("composeWarnings", html)
        self.assertIn("function buildCaseReferenceGuide", html)
        self.assertIn("f_body_en", html)
        self.assertIn("body_en", html)
        self.assertIn("includeBilingual", html)
        self.assertIn("includeModelGuide", html)
        self.assertIn("function modelPromptGuide", html)
        self.assertIn("WAN 3.0", html)
        self.assertIn("MiniMax H3", html)
        self.assertIn("Seedance 2.5", html)
        self.assertIn("RunningHub 是运行平台，不是具体视频模型", html)
        self.assertIn("English Versions", html)
        self.assertIn("复制英文版本", html)
        self.assertIn("includeStructureGuide", html)
        self.assertIn("主参考 > 次参考 > 辅助参考", html)
        self.assertIn("不会自动改写原提示词中的 @图片编号", html)
        self.assertIn("发现组合冲突，已阻止生成", html)
        self.assertIn("composition:", html)
        self.assertIn("base_case_id", html)
        self.assertIn("module_ids", html)
        self.assertIn("组合来源记录", html)
        self.assertIn("原始来源与溯源记录", html)
        self.assertIn("source_prompt", (self.root / "storage.py").read_text(encoding="utf-8"))
        self.assertIn("source_assets", (self.root / "storage.py").read_text(encoding="utf-8"))
        self.assertIn("f_composition", html)
        self.assertIn("JSON.parse($(\"f_composition\")", html)
        self.assertIn("function bindBulkAssetControls", html)
        self.assertIn("applyBulkStatusBtn", html)
        self.assertIn("applyBulkTagBtn", html)
        self.assertIn("deleteBulkBtn", html)
        self.assertIn("selectedCaseIds", html)
        self.assertIn('saveFailureMessage("批量删除",outcome,e)', html)
        self.assertIn("reconcileSaveFailure", html)
        self.assertIn("function bindBulkModuleControls", html)
        self.assertIn("moduleSelectVisibleBtn", html)
        self.assertIn("moduleApplyBulkTagBtn", html)
        self.assertIn("moduleDeleteBulkBtn", html)
        self.assertIn("selectedModuleIds", html)
        self.assertIn("[\"sku\",\"商品款号 / SKU\"]", html)
        self.assertIn("\"name\",\"sku\",\"category\"", html)
        self.assertIn("x.sku,x.style_number,x.product_code", html)
        self.assertIn("m.case.sku||m.case.style_number||m.case.product_code", html)
        self.assertIn("if(!response.ok||result.ok===false)", html)
        self.assertIn("保存失败，原数据已恢复", html)
        self.assertIn("删除失败，原数据已恢复", html)
        self.assertIn("async function deleteCurrent()", html)

    def test_legacy_json_is_migrated_without_replacing_source(self):
        self.assertIn("legacy_json=LEGACY_DATA", self.app)
        self.assertIn("STORAGE.load_data()", self.app)
        self.assertIn("STORAGE.save_data(d)", self.app)


if __name__ == "__main__":
    unittest.main(verbosity=2)
