import shutil
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

    def test_composer_generates_from_module_selection_without_treating_options_as_modules(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "index.html").read_text(encoding="utf-8")
        compose = html.split("function composeNow(){", 1)[1].split("function saveDraft(){", 1)[0]
        self.assertIn(".compose .pane:first-child input[type=checkbox]:checked", compose)
        self.assertNotIn("#content input[type=checkbox]:checked", compose)
        find_conflicts = html.split("function findComposeConflicts(base,mods){", 1)[1].split("function modelPromptGuide(model){", 1)[0]
        model_guide = html.split("function modelPromptGuide(model){", 1)[1].split("function buildCaseReferenceGuide(base){", 1)[0]
        reference_guide = html.split("function buildCaseReferenceGuide(base){", 1)[1].split("function buildCompositionSnapshot(", 1)[0]
        snapshot = html.split("function buildCompositionSnapshot(", 1)[1].split("function compositionForDraft(", 1)[0]
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed in this environment")
        script = r"""
function $(id) { return elements[id]; }
const base = {id:"base-1",name:"Base jacket",model:"WAN 3.0",category:"上装",body:"BASE PROMPT",updated_at:"v1"};
const chosenModule = {id:"module-1",name:"Jacket fabric",model:"通用",category_scope:"上装",body:"SELECTED MODULE BODY",updated_at:"m1"};
const accidental = {id:"on",name:"Must not be included",model:"通用",category_scope:"上装",body:"ACCIDENTAL OPTION BODY"};
const db = {cases:[base],modules:[chosenModule,accidental]};
let lastCompositionSnapshot = null;
const moduleChecks = [{value:"module-1",checked:true},{value:"on",checked:false}];
const rightPaneOptions = [{value:"on",checked:true},{value:"on",checked:true},{value:"on",checked:true}];
const elements = {
  base:{value:"base-1"},
  composeWarnings:{style:{},textContent:""},
  includeStructureGuide:{checked:true},
  includeModelGuide:{checked:true},
  includeBilingual:{checked:false},
  output:{value:""}
};
const document = {
  querySelectorAll(selector) {
    if (selector === ".compose .pane:first-child input[type=checkbox]:checked") return moduleChecks.filter(x=>x.checked);
    if (selector === "#content input[type=checkbox]:checked") return [...moduleChecks,...rightPaneOptions].filter(x=>x.checked);
    throw new Error("Unexpected selector: "+selector);
  }
};
function toast(message) { throw new Error("Unexpected toast: "+message); }
"""
        script += "function findComposeConflicts(base,mods){" + find_conflicts + "\n"
        script += "function modelPromptGuide(model){" + model_guide + "\n"
        script += "function buildCaseReferenceGuide(base){" + reference_guide + "\n"
        script += "function buildCompositionSnapshot(" + snapshot + "\n"
        script += "function composeNow(){" + compose + "\n"
        script += r"""
composeNow();
if (!elements.output.value.includes("SELECTED MODULE BODY")) throw new Error("Selected module body was not composed.");
if (elements.output.value.includes("ACCIDENTAL OPTION BODY")) throw new Error("A non-module checkbox was treated as a module.");
if (!lastCompositionSnapshot || lastCompositionSnapshot.composition.module_ids.join(",") !== "module-1") throw new Error("Composition snapshot must contain only the selected module.");
if (!elements.output.value.includes("BASE PROMPT")) throw new Error("Base prompt was not retained.");
if (!elements.output.value.includes("模型适配提醒")) throw new Error("Selected structure/model options should be reflected in output.");
"""
        result = subprocess.run([node, "-e", script], capture_output=True, text=True, check=False)
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
