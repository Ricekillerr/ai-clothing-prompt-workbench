import ast
import unittest
from html.parser import HTMLParser
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]


class SidebarParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.nav_buttons = []
        self.non_button_nav = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        classes = values.get("class", "").split()
        if "nav" not in classes:
            return
        if tag == "button":
            self.nav_buttons.append(values)
        else:
            self.non_button_nav.append((tag, values))


class NavigationAccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (PROJECT / "index.html").read_text(encoding="utf-8")
        cls.parser = SidebarParser()
        cls.parser.feed(cls.html)

    def test_sidebar_uses_named_native_buttons(self):
        self.assertEqual([], self.parser.non_button_nav)
        self.assertEqual(11, len(self.parser.nav_buttons))
        for button in self.parser.nav_buttons:
            self.assertEqual("button", button.get("type"))
            self.assertTrue(button.get("aria-label", "").strip())
            self.assertTrue(button.get("data-page") or button.get("data-cat"))

    def test_sidebar_version_matches_runtime(self):
        tree = ast.parse((PROJECT / "ai_prompt_workbench.py").read_text(encoding="utf-8"))
        version = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "APP_VERSION"
                for target in node.targets
            ):
                version = ast.literal_eval(node.value)
                break
        self.assertIsInstance(version, str)
        self.assertIn(f"LOCAL WORKSPACE · V{version}<br>仅本机保存", self.html)
        self.assertNotIn("LOCAL WORKSPACE · V0.5.1", self.html)

    def test_existing_navigation_handler_remains_connected(self):
        self.assertIn('document.querySelectorAll(".nav").forEach(n=>n.onclick=', self.html)
        self.assertIn("width:100%;padding:10px 12px", self.html)
        self.assertIn("background:transparent;color:#aeb7c5", self.html)


if __name__ == "__main__":
    unittest.main()
