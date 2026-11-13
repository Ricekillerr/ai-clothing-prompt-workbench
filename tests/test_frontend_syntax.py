import re
import shutil
import subprocess
import unittest
from pathlib import Path


class FrontendSyntaxTests(unittest.TestCase):
    def test_gallery_escapes_imported_timestamps_and_shows_real_update_cadence(self):
        html = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
        self.assertIn(
            '${esc((x.updated_at||x.created_at||"").slice(0,10))}',
            html,
            "Imported timestamp values must be HTML-escaped before insertion into the gallery",
        )
        self.assertNotIn(
            '${(x.updated_at||x.created_at||"").slice(0,10)}',
            html,
            "Raw imported timestamps must never be interpolated into innerHTML",
        )
        self.assertIn("每 15 分钟检查 GitHub 新版本", html)
        self.assertNotIn("每 6 小时自动检查 GitHub 新版本", html)

    def test_prompt_editor_preserves_whitespace_and_json_backup_waits_for_save(self):
        html = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
        self.assertIn('x.body=$("f_body").value;', html)
        self.assertIn('x.body_en=$("f_body_en")?.value||"";', html)
        self.assertNotIn('x.body=$("f_body").value.trim()', html)
        self.assertIn('if(!x.name||!x.body.trim())', html)
        self.assertIn("async function exportJSON(){try{await save()}", html)
        self.assertIn("f.size>25*1024*1024", html)

    def test_legacy_and_imported_tags_are_normalized_to_arrays(self):
        html = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
        self.assertIn("function normalizeTags(value)", html)
        self.assertIn("tags:normalizeTags(item.tags)", html)
        self.assertIn("value.split(/[,，]/)", html)

    def test_gallery_status_and_model_filters_update_state_and_count(self):
        html = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
        self.assertIn('st.onchange=()=>{window.sf=st.value;window.mf=mf.value;render()}', html)
        self.assertIn('mf.onchange=()=>{window.sf=st.value;window.mf=mf.value;render()}', html)
        self.assertNotIn('id="statusF" onchange="render()"', html)
        self.assertNotIn('id="modelF" onchange="render()"', html)
        self.assertIn('textContent=arr.length+" 条资产 · 图片优先的浏览方式"', html)

    def test_composed_prompt_preserves_whitespace_and_checks_category_scope(self):
        html = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
        self.assertIn('function saveDraft(){let body=$("output").value;', html)
        self.assertIn('if(!body.trim())return toast("先生成组合草稿")', html)
        self.assertIn('适用品类不匹配：', html)

    def test_full_backup_restore_and_update_flush_pending_saves_first(self):
        html = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
        restore = html.split("async function restoreFullBackup(file){", 1)[1].split("async function downloadFullBackup()", 1)[0]
        backup = html.split("async function downloadFullBackup(){", 1)[1].split("function importJSON(f)", 1)[0]
        update = html.split("async function applyUpdate(automatic=false){", 1)[1].split("async function scanApi(", 1)[0]
        self.assertLess(restore.index("await save()"), restore.index('fetch("/api/backup/restore"'))
        self.assertLess(backup.index("await save()"), backup.index('fetch("/api/backup/full"'))
        self.assertLess(update.index("await save()"), update.index('fetch("/api/update/apply"'))
        self.assertLess(update.index("updateRequestSent=true"), update.index('fetch("/api/update/apply"'))
        self.assertIn("更新已取消：待保存数据未能确认写入", update)

    def test_load_reports_failure_and_restore_does_not_claim_false_success(self):
        html = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
        load = html.split("async function load(){", 1)[1].split("function save()", 1)[0]
        restore = html.split("async function restoreFullBackup(file){", 1)[1].split("async function downloadFullBackup()", 1)[0]
        self.assertIn("return true", load)
        self.assertIn("return false", load)
        self.assertIn("const loaded=await load()", restore)
        self.assertIn("服务器已确认恢复成功，但界面未能重新读取数据", restore)
        self.assertIn("if(!loaded)", restore)

    def test_bulk_selection_drops_items_hidden_by_current_filters(self):
        html = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
        self.assertIn("pruneSelectionToVisible(selectedCaseIds,arr)", html)
        self.assertIn("pruneSelectionToVisible(selectedModuleIds,a)", html)
        helper = re.search(
            r"function pruneSelectionToVisible\(selection,visibleItems\)\{[^{}]*\}",
            html,
        )
        self.assertIsNotNone(helper, "Selection pruning must remain a small independently testable helper")
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed in this environment")
        script = helper.group(0) + """
const selected = new Set(["visible", "hidden"]);
pruneSelectionToVisible(selected, [{id: "visible"}]);
if (selected.size !== 1 || !selected.has("visible")) process.exit(1);
"""
        result = subprocess.run([node, "-e", script], capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, (result.stderr or result.stdout).decode("utf-8", errors="replace"))

    def test_inline_javascript_parses_when_node_is_available(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed in this environment")
        html = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.I | re.S)
        self.assertTrue(scripts, "index.html should contain its application script")
        result = subprocess.run(
            [node, "--check", "-"],
            input=scripts[-1].encode("utf-8"),
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, (result.stderr or result.stdout).decode("utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
