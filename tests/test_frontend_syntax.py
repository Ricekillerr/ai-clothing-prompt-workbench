import re
import shutil
import subprocess
import unittest
from pathlib import Path


class FrontendSyntaxTests(unittest.TestCase):
    def test_inline_javascript_parses_when_node_is_available(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed in this environment")
        html = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
        scripts = re.findall(r"<script\\b[^>]*>(.*?)</script>", html, flags=re.I | re.S)
        self.assertTrue(scripts, "index.html should contain its application script")
        result = subprocess.run(
            [node, "--check", "-"],
            input=scripts[-1],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
