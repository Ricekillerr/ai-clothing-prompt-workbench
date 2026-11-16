import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tools import review_gate


PROJECT = Path(__file__).resolve().parents[1]


class ReviewGateTests(unittest.TestCase):
    def test_documentation_only_change_skips_conditional_security_job(self):
        report = review_gate.build_report([
            "docs/AGENTIC_REVIEW_POLICY.md",
            ".github/pull_request_template.md",
        ])
        self.assertFalse(report.security_required)
        self.assertEqual([], list(report.private_paths))

    def test_trust_boundaries_select_expected_security_axes(self):
        cases = {
            ".github/workflows/review-gate.yml": "CI、工作流与权限",
            "ai_analyzer.py": "网络、模型凭据与输入反序列化",
            "index.html": "本地 API、浏览器交互与网络请求",
            "storage.py": "SQLite、文件路径、备份与恢复",
            "updater.py": "更新事务与程序替换",
            "package-lock.json": "依赖清单与供应链",
            "启动AI服装提示词工作台.bat": "Shell 与本地命令执行",
        }
        for path, reason in cases.items():
            with self.subTest(path=path):
                report = review_gate.build_report([path])
                self.assertTrue(report.security_required)
                self.assertIn(reason, report.triggers)

    def test_private_data_and_credential_paths_fail_closed(self):
        paths = [
            "DataHome/workbench.sqlite",
            "references/front.png",
            "_backup/index-before.html",
            ".env",
            "config/private.pem",
            "user_config.json",
        ]
        report = review_gate.build_report(paths)
        self.assertEqual(tuple(paths), report.private_paths)
        self.assertIn("阻止", review_gate.render_markdown(report))

    def test_normal_project_tests_and_fixtures_are_not_private_by_suffix_alone(self):
        report = review_gate.build_report([
            "tests/test_storage.py",
            "tests/fixtures/reference.png",
            "docs/MODEL_CONFIGURATION_AND_AGENT_ROADMAP.md",
        ])
        self.assertEqual([], list(report.private_paths))

    def test_path_traversal_and_absolute_paths_are_rejected(self):
        for path in ["../outside.txt", "/tmp/secret.txt", "C:\\secret.txt", "a/../../b"]:
            with self.subTest(path=path):
                with self.assertRaises(review_gate.ReviewGateError):
                    review_gate.build_report([path])

    def test_summary_preserves_loop_cap_and_human_merge_boundary(self):
        markdown = review_gate.render_markdown(review_gate.build_report(["storage.py"]))
        self.assertIn("自动返修上限：2 轮", markdown)
        self.assertIn("第 3 次审查", markdown)
        self.assertIn("维护者始终是合并门禁", markdown)
        self.assertIn("不自动评论、推送、合并或发布", markdown)

    def test_cli_accepts_documentation_and_writes_read_only_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = root / "paths.txt"
            output = root / "output.txt"
            summary = root / "summary.md"
            paths.write_text("docs/AGENTIC_REVIEW_POLICY.md\n", encoding="utf-8")
            with patch.dict(os.environ, {
                "GITHUB_OUTPUT": str(output),
                "GITHUB_STEP_SUMMARY": str(summary),
            }):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    result = review_gate.main(["--paths-file", str(paths)])
            self.assertEqual(0, result)
            self.assertIn("security_required=no", output.read_text(encoding="utf-8"))
            self.assertIn("未触及预定义信任边界", summary.read_text(encoding="utf-8"))

    def test_cli_returns_two_for_private_path(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = Path(temp) / "paths.txt"
            paths.write_text("DataHome/private.sqlite\n", encoding="utf-8")
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = review_gate.main(["--paths-file", str(paths)])
            self.assertEqual(2, result)

    def test_workflow_is_read_only_and_conditional(self):
        workflow = (PROJECT / ".github/workflows/review-gate.yml").read_text(encoding="utf-8")
        self.assertIn("contents: read", workflow)
        self.assertNotIn("pull_request_target", workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertNotIn("pull-requests: write", workflow)
        self.assertNotIn("gh aw", workflow)
        self.assertNotIn("auto-merge", workflow)
        self.assertIn("needs.review-context.outputs.security_required == 'yes'", workflow)
        self.assertIn("timeout-minutes: 10", workflow)


if __name__ == "__main__":
    unittest.main()
