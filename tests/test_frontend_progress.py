import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class FrontendProgressTests(unittest.TestCase):
    def setUp(self):
        self.html = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
        self.node = shutil.which("node")

    def run_node(self, script):
        if not self.node:
            self.skipTest("Node.js is not installed in this environment")
        script_path = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as script_file:
                script_file.write(script)
                script_path = Path(script_file.name)
            result = subprocess.run([self.node, str(script_path)], capture_output=True, text=True, check=False)
        finally:
            if script_path is not None:
                script_path.unlink()
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_scan_batch_reports_each_folder_and_restores_button_state(self):
        self.assertIn(".btn:active:not(:disabled)", self.html)
        self.assertIn(".btn:focus-visible", self.html)
        self.assertIn(".btn:disabled", self.html)
        self.assertIn(".btn[aria-busy=true]", self.html)
        start = self.html.index("let scanBatchInProgress=false;")
        end = self.html.index("let aiAnalysisInProgress=false;", start)
        source = self.html[start:end]
        script = r"""
const history = [];
const status = {};
Object.defineProperty(status, "textContent", { get(){return this.value;}, set(value){this.value=value;history.push(value);} });
const button = { disabled:false, textContent:"立即扫描", attrs:{},
  setAttribute(key,value){this.attrs[key]=value;},
  removeAttribute(key){delete this.attrs[key];}
};
function $(id){return id==="scanStatus"?status:id==="scanQueue"?{}:null;}
let calls=0,queueCalls=0,fail=false;
async function scanApi(path){
  if(path==="/api/scan/sources")return {sources:[{id:"one"},{id:"two"}]};
  calls++;
  if(fail&&calls===2)throw new Error("folder permission denied");
  return {summary:{added:1,changed:2,unchanged:3}};
}
async function loadScanQueue(){queueCalls++;}
"""
        script += source
        script += r"""
async function loadScanQueue(){queueCalls++;}
(async()=>{
 await runAllScans(button);
 if(!history.some(x=>x.includes("1/2"))||!history.some(x=>x.includes("2/2")))throw new Error("Each folder must have a visible progress stage.");
 if(!status.textContent.includes("新增 2")||!status.textContent.includes("变化 4")||!status.textContent.includes("未变化 6"))throw new Error("Final scan totals must be accumulated correctly.");
 if(button.disabled||button.attrs["aria-busy"]!==undefined||button.textContent!=="立即扫描")throw new Error("Successful scan must restore the original button state.");
 if(queueCalls!==1)throw new Error("Successful scan must refresh the review queue.");

 calls=0;fail=true;
 await runAllScans(button);
 if(!status.textContent.includes("folder permission denied"))throw new Error("Scan errors must be visible.");
 if(button.disabled||button.attrs["aria-busy"]!==undefined||button.textContent!=="立即扫描")throw new Error("Failed scan must also restore the original button state.");
})().catch(error=>{console.error(error);process.exit(1);});
"""
        self.run_node(script)

    def test_ai_analysis_prevents_duplicate_requests_and_shows_stages(self):
        start = self.html.index("let aiAnalysisInProgress=false;")
        end = self.html.index("async function loadAiReviews(){", start)
        source = self.html[start:end]
        script = r"""
const status = {textContent:""};
const button = {disabled:false,textContent:"分析这组图片并提取案例",attrs:{},
  setAttribute(key,value){this.attrs[key]=value;},
  removeAttribute(key){delete this.attrs[key];}
};
const fields = {
  scanStatus:status,
  aiKey:{value:"test-key"},
  aiEndpoint:{value:"https://example.invalid/v1"},
  aiModel:{value:"vision-test"}
};
function $(id){return fields[id]||null;}
function confirm(){return true;}
let calls=0,reviewCalls=0,payload=null;
async function scanApi(path,body){
  if(path!=="/api/ai/analyze")throw new Error("Unexpected API route");
  calls++;payload=body;
  await new Promise(resolve=>setTimeout(resolve,20));
  return {ok:true};
}
async function loadAiReviews(){reviewCalls++;}
"""
        script += source
        script += r"""
(async()=>{
 const first=runAiAnalysis({prompt_path:"prompt.txt",asset_paths:["front.png","detail.png"]},button);
 await new Promise(resolve=>setTimeout(resolve,0));
 if(!button.disabled||button.attrs["aria-busy"]!=="true"||!button.textContent.includes("AI 分析中"))throw new Error("AI analysis must visibly lock the active button while running.");
 await runAiAnalysis({prompt_path:"other.txt",asset_paths:["other.png"]},button);
 await first;
 if(calls!==1||reviewCalls!==1)throw new Error("Concurrent clicks must not submit duplicate AI analysis requests.");
 if(payload.api_key!=="test-key"||payload.asset_paths.length!==2)throw new Error("The selected images and session key must be passed to the analysis request.");
 if(button.disabled||button.attrs["aria-busy"]!==undefined||button.textContent!=="分析这组图片并提取案例")throw new Error("AI analysis must restore the button state after completion.");
 if(!status.textContent.includes("人工确认队列"))throw new Error("Completion stage must be visible.");
})().catch(error=>{console.error(error);process.exit(1);});
"""
        self.run_node(script)


    def test_smart_search_ignores_out_of_order_responses(self):
        start = self.html.index("let smartSearchRequestId=0;")
        end = self.html.index("\nfunction gallery(", start)
        source = self.html[start:end]
        script = r"""
const input = {value:"red"};
const status = {textContent:""};
const box = {innerHTML:"", querySelectorAll(){return []; }};
const fields = {
  smartQuery:input,
  smartSearchStatus:status,
  smartSearchResults:box,
  includeConflicts:{checked:false}
};
function $(id){return fields[id]||null;}
const pending=[];
function scanApi(url){return new Promise(resolve=>pending.push({url,resolve}));}
function esc(value){return String(value??"");}
function composeNow(){}
function saveDraft(){}
function copyText(){}
function image(){return "";}
function renderMatchAttributeDetails(){return "";}
function renderMatchConflicts(){return "";}
function openDetail(){}
function recordCaseFeedback(){}
"""
        script += source
        script += r"""
(async()=>{
 const older=runSmartCaseSearch();
 if(pending.length!==1||!pending[0].url.includes("q=red"))throw new Error("First query was not submitted.");
 input.value="black";
 const newer=runSmartCaseSearch();
 if(pending.length!==2||!pending[1].url.includes("q=black"))throw new Error("Second query was not submitted.");
 pending[1].resolve({matches:[]});
 await newer;
 const latestStatus=status.textContent;
 if(!latestStatus.includes("没有找到"))throw new Error("Newest query result was not rendered.");
 pending[0].resolve({matches:[{case:{id:"old",name:"Old result"},match_percent:99,score:0.99,reasons:[]}]});
 await older;
 if(status.textContent!==latestStatus)throw new Error("An older response overwrote the newest search status.");
 if(box.innerHTML!=="")throw new Error("An older response overwrote the newest search results.");
})().catch(error=>{console.error(error);process.exit(1);});
"""
        self.run_node(script)


    def test_gallery_paginates_large_results_and_keeps_bulk_scope_filtered(self):
        self.assertIn('loading="lazy" decoding="async"', self.html)
        start = self.html.index("let galleryPage=1,")
        end = self.html.index("\nfunction updateBulkCount(", start)
        source = self.html[start:end]
        script = r"""
const items=Array.from({length:100},(_,i)=>({id:"case-"+i,name:"Case "+i,status:"已验证成功",category:"上装",model:"通用",tags:[]}));
const sectionText={textContent:""};
const content={innerHTML:"",querySelector(){return sectionText;}};
const openButton={dataset:{openCaseButton:"case-0"},addEventListener(type,fn){this.fn=fn;}};
const grid={innerHTML:"",querySelectorAll(selector){return selector==="[data-open-case-button]"?[openButton]:[];}};
const previous={disabled:false,addEventListener(type,fn){this.fn=fn;}};
const next={disabled:false,addEventListener(type,fn){this.fn=fn;}};
const pager={style:{}};
const pageLabel={textContent:""};
const fields={search:{value:""},content,grid,statusF:{value:"",onchange:null},modelF:{value:"",onchange:null},galleryPrev:previous,galleryNext:next,galleryPagination:pager,galleryPageLabel:pageLabel};
function $(id){return fields[id]||null;}
function setTitle(){}
const db={cases:items};
let filter="全部";
const selectedCaseIds=new Set();
const CATS=["上装"],ST=["已验证成功","待验证","失败案例"],MODELS=["通用"];
const window={sf:"",mf:""};
function pruneSelectionToVisible(){}
function esc(value){return String(value??"");}
function image(){return "<img>";}
let bulkCount=0,openedCase=null;
function openDetail(id){openedCase=id;}
function bindBulkAssetControls(arr){bulkCount=arr.length;}
function render(){gallery();}
"""
        script += source
        script += r"""
gallery();
let stopped=false;openButton.fn({stopPropagation(){stopped=true;}});
if(openedCase!=="case-0"||!stopped)throw new Error("Keyboard-accessible open button must open the matching case without bubbling.");
const count=()=> (grid.innerHTML.match(/data-open-case=/g)||[]).length;
if(count()!==48)throw new Error("First page must render 48 cards; got "+count());
if(bulkCount!==100)throw new Error("Bulk management must retain the full filtered result set.");
if(!grid.innerHTML.includes('data-open-case="case-0"')||grid.innerHTML.includes('data-open-case="case-48"'))throw new Error("First page boundaries are incorrect.");
if(!next.fn||!previous.disabled||next.disabled)throw new Error("Pagination controls must reflect the first page.");
next.fn();
if(count()!==48||!grid.innerHTML.includes('data-open-case="case-48"')||!grid.innerHTML.includes('data-open-case="case-95"'))throw new Error("Next page must render cases 48 through 95.");
if(pageLabel.textContent!=="第 2 / 3 页")throw new Error("Page label did not update.");
previous.fn();
if(!grid.innerHTML.includes('data-open-case="case-0"'))throw new Error("Previous page did not return to page one.");
fields.search.value="case 9";
gallery();
if(count()!==11||bulkCount!==11||pageLabel.textContent!=="第 1 / 1 页"||pager.style.display!=="none")throw new Error("Search must reset pagination and bulk scope to the filtered results.");
"""
        self.run_node(script)


    def test_module_library_paginates_and_keeps_bulk_scope_filtered(self):
        start = self.html.index("let modulePage=1,")
        end = self.html.index("\nlet vaultPage=1,", start)
        source = self.html[start:end]
        script = r"""
const items=Array.from({length:80},(_,i)=>({id:"module-"+i,name:"Module "+i,module_type:"Fabric",category_scope:"上装",material:"nylon",body:"prompt "+i,body_en:"",tags:[],model:"WAN3.0"}));
const content={innerHTML:""};
const previous={disabled:false,addEventListener(type,fn){this.fn=fn;}};
const next={disabled:false,addEventListener(type,fn){this.fn=fn;}};
const fields={search:{value:""},content,modulePagination:{style:{}},modulePrev:previous,moduleNext:next,modulePageLabel:{textContent:""}};
const pager=fields.modulePagination,pageLabel=fields.modulePageLabel;
function $(id){return fields[id]||null;}
function setTitle(){}
const db={modules:items};
const selectedModuleIds=new Set();
function pruneSelectionToVisible(){}
function esc(value){return String(value??"");}
const document={querySelectorAll(){return [];}};
let bulkCount=0;
function bindBulkModuleControls(arr){bulkCount=arr.length;}
function render(){modules();}
"""
        script += source
        script += r"""
modules();
const count=()=> (content.innerHTML.match(/data-open-module="module-/g)||[]).length;
if(count()!==36||bulkCount!==80)throw new Error("Module page one must render 36 cards while bulk scope keeps all 80.");
if(!next.fn||!previous.disabled)throw new Error("Module pagination controls are not initialized.");
next.fn();
if(count()!==36||!content.innerHTML.includes('data-open-module="module-36"')||!content.innerHTML.includes('data-open-module="module-71"'))throw new Error("Module page two boundaries are incorrect.");
if(pageLabel.textContent!=="第 2 / 3 页")throw new Error("Module page label did not update.");
previous.fn();
fields.search.value="module 7";
modules();
if(count()!==11||bulkCount!==11||pageLabel.textContent!=="第 1 / 1 页"||pager.style.display!=="none")throw new Error("Module search must reset page and bulk scope to filtered results.");
"""
        self.run_node(script)

    def test_prompt_vault_paginates_large_full_prompt_cards(self):
        start = self.html.index("let vaultPage=1,")
        end = self.html.index("\nlet lastCompositionSnapshot", start)
        source = self.html[start:end]
        script = r"""
const items=Array.from({length:100},(_,i)=>({id:"case-"+i,name:"Case "+i,body:"Prompt body "+i,body_en:"",material:"nylon",tags:[],category:"上装",model:"WAN3.0"}));
const content={innerHTML:"",querySelectorAll(){return [];}};
const previous={disabled:false,addEventListener(type,fn){this.fn=fn;}};
const next={disabled:false,addEventListener(type,fn){this.fn=fn;}};
const pager={style:{}},pageLabel={textContent:""};
const fields={search:{value:""},content,vaultPagination:pager,vaultPrev:previous,vaultNext:next,vaultPageLabel:pageLabel};
function $(id){return fields[id]||null;}
function setTitle(){}
const db={cases:items};
function esc(value){return String(value??"");}
function copyText(){}
function openDetail(){}
function render(){vault();}
"""
        script += source
        script += r"""
vault();
const count=()=> (content.innerHTML.match(/data-copy-body=/g)||[]).length;
if(count()!==20)throw new Error("Prompt Vault must render 20 full-prompt cards per page.");
if(!next.fn||!previous.disabled||pager.style.display!=="flex")throw new Error("Vault pagination controls are not initialized.");
next.fn();
if(count()!==20||!content.innerHTML.includes('data-copy-body="Prompt body 20"')||!content.innerHTML.includes('data-copy-body="Prompt body 39"'))throw new Error("Vault page two boundaries are incorrect.");
if(pageLabel.textContent!=="第 2 / 5 页")throw new Error("Vault page label did not update.");
fields.search.value="case 9";
vault();
if(count()!==11||pageLabel.textContent!=="第 1 / 1 页"||pager.style.display!=="none")throw new Error("Vault search must reset page and hide the one-page pager.");
"""
        self.run_node(script)


    def test_trash_page_paginates_deleted_assets_and_resets_search(self):
        start = self.html.index("let trashPageNum=1,")
        end = self.html.index("\nasync function restoreDeletedAsset", start)
        source = self.html[start:end]
        script = r"""
const items=Array.from({length:105},(_,i)=>({asset_id:"asset-"+i,revision_id:"rev-"+i,kind:"case",category:"上装",name:"Case "+i,sku:"SKU"+i,deleted_at:"2026-11-14",summary:"summary "+i,image:"thumb.png"}));
let deletedAssetsCache=items,trashLoading=false,page="trash";
const content={innerHTML:""};
const previous={disabled:false,addEventListener(type,fn){this.fn=fn;}};
const next={disabled:false,addEventListener(type,fn){this.fn=fn;}};
const pager={style:{}},pageLabel={textContent:""};
const fields={search:{value:""},content,trashPagination:pager,trashPrev:previous,trashNext:next,trashPageLabel:pageLabel};
function $(id){return fields[id]||null;}
function setTitle(){}
function image(){return "<img>";}
function esc(value){return String(value??"");}
function restoreDeletedAsset(){}
const document={querySelectorAll(){return [];}};
"""
        script += source
        script += r"""
(async()=>{
await trashPage();
const count=()=> (content.innerHTML.match(/data-trash-restore=/g)||[]).length;
if(count()!==50||pageLabel.textContent!=="第 1 / 3 页"||pager.style.display!=="flex")throw new Error("Trash page one must render 50 assets.");
if(!next.fn||!previous.disabled)throw new Error("Trash pagination controls are not initialized.");
await next.fn();
if(count()!==50||!content.innerHTML.includes('data-trash-restore="asset-50"')||!content.innerHTML.includes('data-trash-restore="asset-99"'))throw new Error("Trash page two boundaries are incorrect.");
if(pageLabel.textContent!=="第 2 / 3 页")throw new Error("Trash page label did not update.");
await previous.fn();
if(!content.innerHTML.includes('data-trash-restore="asset-0"'))throw new Error("Trash previous page did not return to page one.");
fields.search.value="case 9";
await trashPage();
if(count()!==11||pageLabel.textContent!=="第 1 / 1 页"||pager.style.display!=="none")throw new Error("Trash search must reset pagination and hide the one-page pager.");
})().catch(error=>{throw error;});
"""
        self.run_node(script)


    def test_composer_module_picker_paginates_and_preserves_selection(self):
        start = self.html.index("let composeModulePage=1,")
        end = self.html.index("\nfunction composeNow(", start)
        source = self.html[start:end]
        script = r"""
const modules=Array.from({length:80},(_,i)=>({id:"module-"+i,name:"Module "+i,module_type:"Fabric",category_scope:"上装",material:"nylon",body:"prompt "+i}));
const content={innerHTML:""};
const list={innerHTML:"",querySelectorAll(){return [];}};
const search={value:"",addEventListener(type,fn){this.oninput=fn;}};
const pager={style:{}},previous={disabled:false},next={disabled:false},pageLabel={textContent:""},selectionStatus={textContent:""};
const fields={content,base:{value:""},composeModuleSearch:search,composeModuleList:list,composeModulePagination:pager,composeModulePrev:previous,composeModuleNext:next,composeModulePageLabel:pageLabel,composeModuleSelectionStatus:selectionStatus,composeNowBtn:{addEventListener(){}},copyCompositionBtn:{addEventListener(){}},saveCompositionBtn:{addEventListener(){}}};
function $(id){return fields[id]||null;}
function setTitle(){}
const db={cases:[{id:"good",name:"Good",category:"上装",status:"已验证成功",body:"valid prompt"},{id:"pending",name:"Pending",status:"待验证",body:"pending prompt"},{id:"failed",name:"Failed",status:"失败案例",body:"failed prompt"}],modules};
let lastCompositionSnapshot=null;
function esc(value){return String(value??"");}
function composeNow(){}
function copyText(){}
function saveDraft(){}
"""
        script += source
        script += r"""
compose();
const count=()=> (list.innerHTML.match(/class="check"/g)||[]).length;
if(!content.innerHTML.includes('value="good"')||content.innerHTML.includes('value="pending"')||content.innerHTML.includes('value="failed"'))throw new Error("Composer base selector must only include verified complete cases.");
if(count()!==30||pageLabel.textContent!=="第 1 / 3 页 · 80 个匹配"||pager.style.display!=="flex")throw new Error("Composer module page one is incorrect.");
composeSelectedModuleIds.add("module-0");
next.onclick();
if(count()!==30||!list.innerHTML.includes('value="module-30"')||!list.innerHTML.includes('value="module-59"'))throw new Error("Composer module page two boundaries are incorrect.");
if(selectionStatus.textContent!=="已选 1 个模块")throw new Error("Selected modules must persist across pages.");
previous.onclick();
if(!list.innerHTML.includes('value="module-0" checked'))throw new Error("Previously selected module must remain checked when returning to its page.");
search.value="module 7";
search.oninput();
if(count()!==11||pageLabel.textContent!=="第 1 / 1 页 · 11 个匹配"||pager.style.display!=="none"||selectionStatus.textContent!=="已选 1 个模块")throw new Error("Composer search must reset page while retaining prior selection.");
"""
        self.run_node(script)

    def test_compose_now_rejects_unverified_base_and_uses_persistent_module_selection(self):
        start = self.html.index("function composeNow(){")
        end = self.html.index("\nfunction saveDraft", start)
        source = self.html[start:end]
        script = r"""
const fields={base:{value:"good"},composeWarnings:{style:{},textContent:""},includeStructureGuide:{checked:false},includeModelGuide:{checked:false},includeBilingual:{checked:false},output:{value:""}};
function $(id){return fields[id]||null;}
const db={cases:[{id:"good",name:"Good",status:"已验证成功",body:"base prompt",body_en:"",model:"WAN3.0"},{id:"pending",name:"Pending",status:"待验证",body:"pending prompt",body_en:"",model:"WAN3.0"}],modules:[{id:"selected",name:"Selected",body:"selected module body",body_en:""},{id:"other",name:"Other",body:"other module body",body_en:""}]};
const composeSelectedModuleIds=new Set(["selected"]);
let lastCompositionSnapshot=null,toastMessage="";
function toast(message){toastMessage=message;}
function findComposeConflicts(){return [];}
function buildCaseReferenceGuide(){return "";}
function modelPromptGuide(){return "";}
function buildCompositionSnapshot(base,mods,body){return {baseId:base.id,moduleIds:mods.map(x=>x.id),body};}
"""
        script += source
        script += r"""
composeNow();
if(!fields.output.value.includes("selected module body")||fields.output.value.includes("other module body"))throw new Error("Composer must include only selected persistent module IDs.");
if(!lastCompositionSnapshot||lastCompositionSnapshot.moduleIds.join(",")!=="selected")throw new Error("Composition provenance must include only selected module IDs.");
fields.base.value="pending";
composeNow();
if(lastCompositionSnapshot!==null||fields.output.value!==""||!toastMessage.includes("已验证成功"))throw new Error("Unverified base must clear stale output and refuse composition.");
"""
        self.run_node(script)


if __name__ == "__main__":
    unittest.main(verbosity=2)
