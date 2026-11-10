import ast
import unittest
from pathlib import Path


class PythonSourceSyntaxTests(unittest.TestCase):
    def test_all_application_python_files_parse(self):
        root = Path(__file__).resolve().parents[1]
        files = [
            "ai_prompt_workbench.py", "storage.py", "collector.py",
            "case_matcher.py", "ai_analyzer.py", "updater.py",
        ]
        for name in files:
            with self.subTest(file=name):
                ast.parse((root / name).read_text(encoding="utf-8"), filename=name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
