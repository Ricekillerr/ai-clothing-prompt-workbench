import subprocess
import unittest
from pathlib import Path


class PromptComposerConflictTests(unittest.TestCase):
    def test_composer_draft_provenance_uses_generation_time_snapshot(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "index.html").read_text(encoding="utf-8")
        start = html.index("function buildCompositionSnapshot(")
        end = html.index("function composeNow()", start)
        helpers = html[start:end]
        self.assertIn("lastCompositionSnapshot=buildCompositionSnapshot(b,mods,generatedBody)", html)
        self.assertIn("compositionForDraft(body,lastCompositionSnapshot)", html)
        script = helpers + r"""
const base = {id: "case-A", name: "Original base", updated_at: "v1"};
const moduleA = {id: "module-A", name: "Original module", updated_at: "m1"};
const snapshot = buildCompositionSnapshot(base, [moduleA], "generated prompt", "2026-11-13T10:00:00Z");
const unchanged = compositionForDraft("generated prompt", snapshot, "later");
if (unchanged.base_case_id !== "case-A" || unchanged.module_ids[0] !== "module-A") {
  throw new Error("Unchanged output must retain the exact generation-time source IDs.");
}
if (unchanged.preflight !== "passed" || unchanged.modified_after_generation !== false) {
  throw new Error("Unchanged output must retain its passed preflight state.");
}
const edited = compositionForDraft("manually edited prompt", snapshot, "later");
if (edited.base_case_id !== "case-A" || edited.module_ids[0] !== "module-A") {
  throw new Error("Editing the current selectors must not rewrite the source snapshot.");
}
if (edited.preflight !== "passed_then_manually_edited" || edited.modified_after_generation !== true) {
  throw new Error("Manual edits must be clearly disclosed in provenance.");
}
const manual = compositionForDraft("typed prompt", null, "now");
if (manual.source_snapshot_available !== false || manual.preflight !== "not_generated_by_composer") {
  throw new Error("Manually entered content must not claim composer provenance.");
}
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_cross_module_type_conflicts_are_detected_but_distinct_garment_scopes_are_isolated(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "index.html").read_text(encoding="utf-8")
        start = html.index("function findComposeConflicts(base,mods){")
        end = html.index("function modelPromptGuide(model){", start)
        function_source = html[start:end]
        script = function_source + r"""
const base = {name: "Base", model: "WAN 3.0", body: ""};
const openZipper = {
  name: "Open zipper", model: "通用", category_scope: "上装",
  module_type: "款式与结构", body: "拉链完全打开"
};
const closedZipper = {
  name: "Closed zipper", model: "通用", category_scope: "上装",
  module_type: "换装约束", body: "拉链完全闭合"
};
let issues = findComposeConflicts(base, [openZipper, closedZipper]);
if (!issues.some(x => x.includes("拉链状态"))) {
  throw new Error("Expected zipper conflict across different module types.");
}
const trousers = {
  name: "Trouser detail", model: "通用", category_scope: "下装",
  module_type: "其他", body: "拉链完全闭合"
};
issues = findComposeConflicts(base, [openZipper, trousers]);
if (issues.some(x => x.includes("拉链状态"))) {
  throw new Error("Different garment scopes must not be treated as the same zipper.");
}
const wrongModel = {
  name: "Seedance-only module", model: "Seedance 2.5",
  category_scope: "上装", module_type: "其他", body: ""
};
issues = findComposeConflicts(base, [wrongModel]);
if (!issues.some(x => x.includes("模型不兼容"))) {
  throw new Error("Expected model incompatibility to remain enforced.");
}
"""
        result = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
