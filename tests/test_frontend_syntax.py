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
        self.assertIn("function saveDraft(){", html)
        self.assertIn("const composition=compositionForDraft(body,lastCompositionSnapshot)", html)
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

    def test_recycle_bin_restore_requires_the_asset_to_still_be_absent(self):
        html = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
        restore = html.split("async function restoreDeletedAsset(assetId,revisionId,button){", 1)[1].split("async function trashPage()", 1)[0]
        self.assertIn("expected_absent:true", restore)

    def test_restoring_an_asset_revision_refreshes_the_background_library(self):
        html = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
        restore = html.split("async function restoreAssetRevision(revisionId){", 1)[1].split("function closeDetail()", 1)[0]
        self.assertLess(restore.index("db=normalizeAssetData(data.data)"), restore.index("render()"))
        self.assertIn("render();openDetail(chosen,kind);showAssetHistory()", restore)

    def test_json_import_merge_is_additive_and_deduplicates_normalized_ids(self):
        html = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
        self.assertIn("reader.onerror=()=>toast(", html)
        self.assertIn('e.target.value="";if(file)importJSON(file)', html)
        start = html.index("function mergeImportedAssets(")
        end = html.index("function importJSON(f)", start)
        helper = html[start:end]
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed in this environment")
        script = html[html.index("function normalizeTags(value){"):html.index("function normalizeAssetRows(", html.index("function normalizeTags(value){"))] + helper + """
const current = {
  cases: [{id: " existing ", name: "Existing"}],
  modules: [{id: "module-1", name: "Module"}]
};
const originalCases = current.cases.slice(), originalModules = current.modules.slice();
const result = mergeImportedAssets({
  cases: [
    {id: "existing", name: "Duplicate existing"},
    {id: "new-case", name: "New case", tags: "one, two", image: 12},
    {id: "new-case", name: "Duplicate in batch"},
    null,
    {id: "   ", name: "Blank ID"}
  ],
  modules: [
    {id: "new-case", name: "Cross-kind duplicate"},
    {id: "new-module", name: "New module", tags: [" x ", null, "y"], image: "ref.png"},
    {id: 42, name: "Numeric legacy ID"},
    {id: {}, name: "Invalid ID type"}
  ]
}, current);
if (result.added !== 3 || result.skipped !== 6) throw new Error("Unexpected additive import counts: " + JSON.stringify(result));
if (result.data.cases.length !== 2 || result.data.modules.length !== 3) throw new Error("Cross-kind or in-batch duplicates were not skipped.");
if (result.data.cases[1].id !== "new-case" || result.data.cases[1].tags.join("|") !== "one|two" || result.data.cases[1].image !== "") throw new Error("Imported case fields were not normalized.");
if (result.data.modules[1].tags.join("|") !== "x|y" || result.data.modules[1].image !== "ref.png" || result.data.modules[2].id !== "42") throw new Error("Imported module fields were not normalized.");
if (current.cases[0] !== originalCases[0] || current.modules[0] !== originalModules[0]) throw new Error("Merge must not mutate the current arrays before save.");
"""
        result = subprocess.run([node, "-e", script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_server_refresh_paths_share_legacy_data_normalization(self):
        html = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
        for required in (
            "db=normalizeAssetData(data)",
            "db=normalizeAssetData(await response.json())",
            "const latest=normalizeAssetData(await check.json())",
            "db=normalizeAssetData(payload.data)",
            "db=normalizeAssetData(data.data)",
            'db=normalizeAssetData(d.data)',
        ):
            self.assertIn(required, html)
        self.assertNotIn("db=payload.data", html)
        self.assertNotIn("db=data.data", html)
        self.assertNotIn("db=d.data", html)
        start = html.index("function normalizeTags(value){")
        end = html.index("function pruneSelectionToVisible(", start)
        helpers = html[start:end]
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed in this environment")
        script = helpers + """
const data = normalizeAssetData({
  cases: [{id: "case-1", tags: "outerwear, black", image: 12, created_at: 5, updated_at: null}, null, []],
  modules: [{id: "module-1", tags: [" fabric ", null, "zipper"], image: "ref.png", created_at: "today"}]
});
if (data.cases.length !== 1 || data.cases[0].tags.join("|") !== "outerwear|black") throw new Error("Legacy string tags were not normalized.");
if (data.cases[0].image !== "" || data.cases[0].created_at !== "" || data.cases[0].updated_at !== "") throw new Error("Invalid optional fields were not normalized.");
if (data.modules.length !== 1 || data.modules[0].tags.join("|") !== "fabric|zipper" || data.modules[0].image !== "ref.png") throw new Error("Array tags or valid image paths were not preserved.");
let rejected = false;
try { normalizeAssetData({cases: [], modules: null}); } catch (_) { rejected = true; }
if (!rejected) throw new Error("Invalid server payload should be rejected.");
"""
        result = subprocess.run([node, "-e", script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

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

    def test_bulk_case_delete_confirms_and_rolls_back_failed_save(self):
        html = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
        start = html.index("function updateBulkCount(){")
        end = html.index("function bindBulkModuleControls(", start)
        helpers = html[start:end]
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed in this environment")
        script = helpers + r"""
const caseA = {id:"case-A"}, caseB = {id:"case-B"};
let db = {cases:[caseA,caseB]};
const selectedCaseIds = new Set(["case-A"]);
const controls = {
  bulkCount:{}, selectVisibleBtn:{}, clearSelectionBtn:{},
  bulkStatus:{value:""}, applyBulkStatusBtn:{}, bulkTagInput:{value:""},
  applyBulkTagBtn:{}, deleteBulkBtn:{}
};
function $(id) { return controls[id]; }
const document = {querySelectorAll(){return [];}};
let confirmed = true, failSave = false, galleryCalls = 0, toastMessage = "";
function confirm(){return confirmed;}
function gallery(){galleryCalls++;}
function toast(message){toastMessage=message;}
function save(){return failSave?Promise.reject(new Error("simulated write failure")):Promise.resolve();}
async function reconcileSaveFailure(error,rollback){rollback();return "rolled_back";}
function saveFailureMessage(){return "保存失败，已回滚";}
async function run(){
 bindBulkAssetControls([caseA,caseB]);
 await controls.deleteBulkBtn.onclick();
 if(db.cases.length!==1||db.cases[0].id!=="case-B")throw new Error("Confirmed bulk delete must remove only selected cases.");
 if(selectedCaseIds.size!==0||galleryCalls!==1)throw new Error("Successful bulk delete must clear removed selections and refresh the gallery.");
 if(!toastMessage.includes("已删除"))throw new Error("Successful bulk delete must report completion.");

 db={cases:[caseA,caseB]};
 selectedCaseIds.clear();selectedCaseIds.add("case-A");
 confirmed=false;galleryCalls=0;toastMessage="";
 await controls.deleteBulkBtn.onclick();
 if(db.cases.length!==2||galleryCalls!==0)throw new Error("Cancel must leave data and UI unchanged.");

 confirmed=true;failSave=true;toastMessage="";
 await controls.deleteBulkBtn.onclick();
 if(db.cases.length!==2||db.cases[0].id!=="case-A"||selectedCaseIds.size!==1)throw new Error("Failed bulk save must restore the original cases and selection.");
 if(!toastMessage.includes("保存失败"))throw new Error("Failed bulk save must report the rollback.");
}
run().catch(error=>{console.error(error);process.exit(1);});
"""
        result = subprocess.run([node, "-e", script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_bulk_module_delete_confirms_and_rolls_back_failed_save(self):
        html = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
        start = html.index("function bindBulkModuleControls(")
        end = html.index("function modules(){", start)
        helper = html[start:end]
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed in this environment")
        script = helper + r"""
const moduleA={id:"module-A"},moduleB={id:"module-B"};
let db={modules:[moduleA,moduleB]};
const selectedModuleIds=new Set(["module-A"]);
const controls={
  moduleBulkCount:{},moduleSelectVisibleBtn:{},moduleClearSelectionBtn:{},
  moduleBulkTagInput:{value:""},moduleApplyBulkTagBtn:{},moduleDeleteBulkBtn:{}
};
function $(id){return controls[id];}
const document={querySelectorAll(){return [];}};
let confirmed=true,failSave=false,renderCalls=0,toastMessage="";
function confirm(){return confirmed;}
function render(){renderCalls++;}
function toast(message){toastMessage=message;}
function save(){return failSave?Promise.reject(new Error("simulated write failure")):Promise.resolve();}
async function reconcileSaveFailure(error,rollback){rollback();return "rolled_back";}
function saveFailureMessage(){return "保存失败，已回滚";}
async function run(){
 bindBulkModuleControls([moduleA,moduleB]);
 await controls.moduleDeleteBulkBtn.onclick();
 if(db.modules.length!==1||db.modules[0].id!=="module-B")throw new Error("Confirmed bulk delete must remove only selected modules.");
 if(selectedModuleIds.size!==0||renderCalls!==1)throw new Error("Successful bulk delete must clear selection and rerender.");
 if(!toastMessage.includes("已删除"))throw new Error("Successful module deletion must report completion.");

 db={modules:[moduleA,moduleB]};selectedModuleIds.clear();selectedModuleIds.add("module-A");
 confirmed=false;renderCalls=0;toastMessage="";
 await controls.moduleDeleteBulkBtn.onclick();
 if(db.modules.length!==2||renderCalls!==0)throw new Error("Cancel must leave modules unchanged.");

 confirmed=true;failSave=true;toastMessage="";
 await controls.moduleDeleteBulkBtn.onclick();
 if(db.modules.length!==2||db.modules[0].id!=="module-A"||selectedModuleIds.size!==1)throw new Error("Failed module delete must restore data and selection.");
 if(!toastMessage.includes("保存失败"))throw new Error("Failed module delete must report rollback.");
}
run().catch(error=>{console.error(error);process.exit(1);});
"""
        result = subprocess.run([node, "-e", script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_revision_restore_ui_refreshes_data_and_reports_failure(self):
        html = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
        restore = html.split("async function restoreAssetRevision(revisionId){", 1)[1].split("function closeDetail()", 1)[0]
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed in this environment")
        script = r"""
let db={cases:[{id:"case-1",body:"current"}],modules:[]};
let chosen="case-1",kind="case",toastMessage="",renderCalls=0,detailCalls=0,historyCalls=0,requestBody=null;
function confirm(){return true;}
function normalizeAssetData(data){return data;}
function render(){renderCalls++;}
function openDetail(id,k){if(id!=="case-1"||k!=="case")throw new Error("Wrong detail target");detailCalls++;}
function showAssetHistory(){historyCalls++;}
function toast(message){toastMessage=message;}
let fetchShouldFail=false;
async function fetch(url,options){
 if(url!=="/api/assets/revisions/restore"||options.method!=="POST")throw new Error("Unexpected restore request");
 requestBody=JSON.parse(options.body);
 if(fetchShouldFail)return {ok:false,json:async()=>({error:"simulated restore failure"})};
 return {ok:true,json:async()=>({ok:true,data:{cases:[{id:"case-1",body:"restored old prompt"}],modules:[]}})};
}
"""
        script += "async function restoreAssetRevision(revisionId){" + restore + "\n"
        script += r"""
(async()=>{
 await restoreAssetRevision("revision-1");
 if(requestBody.asset_id!=="case-1"||requestBody.revision_id!=="revision-1")throw new Error("Restore request must identify the selected asset and revision.");
 if(db.cases[0].body!=="restored old prompt"||renderCalls!==1||detailCalls!==1||historyCalls!==1)throw new Error("Successful restore must refresh data, gallery, detail and history.");
 if(!toastMessage.includes("已恢复"))throw new Error("Successful restore must notify the user.");
 db={cases:[{id:"case-1",body:"keep current"}],modules:[]};
 fetchShouldFail=true;toastMessage="";
 await restoreAssetRevision("revision-2");
 if(db.cases[0].body!=="keep current")throw new Error("Failed restore must not replace local data.");
 if(!toastMessage.includes("simulated restore failure"))throw new Error("Failed restore must report the server error.");
})().catch(error=>{console.error(error);process.exit(1);});
"""
        result = subprocess.run([node, "-e", script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_recycle_restore_ui_sends_stale_snapshot_guard_and_recovers_button_on_error(self):
        html = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
        restore = html.split("async function restoreDeletedAsset(assetId,revisionId,button){", 1)[1].split("function settings(){", 1)[0]
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed in this environment")
        script = r"""
let db={cases:[],modules:[]},deletedAssetsCache=[{asset_id:"old"}];
const button={disabled:false,textContent:"恢复资产"};
let toastMessage="",statsCalls=0,trashCalls=0,requestBody=null,fail=false;
function confirm(){return true;}
function normalizeAssetData(data){return data;}
function stats(){statsCalls++;}
function toast(message){toastMessage=message;}
async function trashPage(){trashCalls++;}
async function fetch(url,options){
 if(url!=="/api/assets/revisions/restore"||options.method!=="POST")throw new Error("Unexpected restore request");
 requestBody=JSON.parse(options.body);
 return fail
  ? {ok:false,json:async()=>({error:"stale recycle snapshot"})}
  : {ok:true,json:async()=>({ok:true,data:{cases:[{id:"case-1",body:"restored"}],modules:[]}})};
}
"""
        script += "async function restoreDeletedAsset(assetId,revisionId,button){" + restore + "\n"
        script += r"""
(async()=>{
 await restoreDeletedAsset("case-1","deleted-revision",button);
 if(requestBody.expected_absent!==true||requestBody.revision_id!=="deleted-revision")throw new Error("Recycle restore must require absent asset and exact deletion revision.");
 if(db.cases[0].body!=="restored"||deletedAssetsCache!==null||statsCalls!==1||trashCalls!==1)throw new Error("Successful recycle restore must refresh data, stats and recycle list.");
 if(!toastMessage.includes("资产已恢复"))throw new Error("Successful recycle restore must notify the user.");

 fail=true;button.disabled=false;button.textContent="恢复资产";toastMessage="";
 await restoreDeletedAsset("case-1","stale-revision",button);
 if(!button.disabled===false)throw new Error("Failed restore should leave the button enabled.");
 if(button.textContent!=="恢复资产"||!toastMessage.includes("stale recycle snapshot"))throw new Error("Failed restore must restore button state and show the server error.");
})().catch(error=>{console.error(error);process.exit(1);});
"""
        result = subprocess.run([node, "-e", script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

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
