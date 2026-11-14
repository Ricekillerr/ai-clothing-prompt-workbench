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


if __name__ == "__main__":
    unittest.main(verbosity=2)
