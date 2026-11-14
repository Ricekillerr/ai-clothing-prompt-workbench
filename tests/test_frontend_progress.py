import shutil
import subprocess
import unittest
from pathlib import Path


class FrontendProgressTests(unittest.TestCase):
    def setUp(self):
        self.html = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
        self.node = shutil.which("node")

    def run_node(self, script):
        if not self.node:
            self.skipTest("Node.js is not installed in this environment")
        result = subprocess.run([self.node, "-e", script], capture_output=True, text=True, check=False)
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
const grid={innerHTML:"",querySelectorAll(){return [];}};
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
let bulkCount=0;
function bindBulkAssetControls(arr){bulkCount=arr.length;}
function render(){gallery();}
"""
        script += source
        script += r"""
gallery();
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
