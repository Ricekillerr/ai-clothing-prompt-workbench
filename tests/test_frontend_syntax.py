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
