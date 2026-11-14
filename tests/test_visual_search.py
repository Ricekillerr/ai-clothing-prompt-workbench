import shutil
import subprocess
import unittest
from pathlib import Path


class VisualSearchTests(unittest.TestCase):
    def setUp(self):
        self.html = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
        self.node = shutil.which("node")

    def run_node(self, script):
        if not self.node:
            self.skipTest("Node.js is not installed in this environment")
        result = subprocess.run([self.node, "-e", script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_perceptual_hash_uses_63_non_dc_bits_and_guards_large_images(self):
        start = self.html.index("const VISUAL_HASH_BITS=63;")
        end = self.html.index("async function runVisualCaseSearch(file){", start)
        hash_code = self.html[start:end]
        script = r"""
let activeBlob = null;
global.URL = {
  createObjectURL(blob) { activeBlob = blob; return "blob:visual-test"; },
  revokeObjectURL() {}
};
class FakeImage {
  set src(value) {
    this.naturalWidth = activeBlob.width || 32;
    this.naturalHeight = activeBlob.height || 32;
    setTimeout(() => { if (this.onload) this.onload(); }, 0);
  }
}
global.Image = FakeImage;
global.document = {
  createElement(tag) {
    if (tag !== "canvas") throw new Error("Unexpected element");
    return {
      getContext() {
        return {
          drawImage() {},
          getImageData() { return { data: activeBlob.data }; }
        };
      }
    };
  }
};
function pixels(offset) {
  const data = new Uint8ClampedArray(32 * 32 * 4);
  for (let y = 0; y < 32; y++) for (let x = 0; x < 32; x++) {
    const i = (y * 32 + x) * 4;
    const value = 30 + ((x * 7 + y * 11 + (x * y) % 19) % 150) + offset;
    data[i] = value; data[i + 1] = value; data[i + 2] = value; data[i + 3] = 255;
  }
  return data;
}
"""
        script += hash_code
        script += r"""
(async () => {
  const original = { width: 32, height: 32, data: pixels(0) };
  const brighter = { width: 32, height: 32, data: pixels(20) };
  const hashA = await imagePerceptualHash(original);
  const hashB = await imagePerceptualHash(brighter);
  if (hashA.length !== 63 || !/^[01]{63}$/.test(hashA)) throw new Error("pHash must be exactly 63 binary bits with the DC coefficient excluded.");
  if (imageHashDistance(hashA, hashB) > 2) throw new Error("A uniform brightness shift should not materially change the non-DC pHash.");
  if (imageHashDistance(hashA, hashA) !== 0) throw new Error("Identical hashes must have zero distance.");
  let invalidRejected = false;
  try { imageHashDistance(hashA, hashA.slice(1)); } catch (error) { invalidRejected = error.message.includes("格式无效"); }
  if (!invalidRejected) throw new Error("Mismatched hash lengths must be rejected.");
  let oversizedRejected = false;
  try { await imagePerceptualHash({ width: 7000, height: 7000, data: original.data }); }
  catch (error) { oversizedRejected = error.message.includes("4000万像素"); }
  if (!oversizedRejected) throw new Error("Images over the pixel limit must be rejected before canvas allocation.");
})().catch(error => { console.error(error); process.exit(1); });
"""
        self.run_node(script)

    def test_visual_search_filters_verified_cases_deduplicates_and_reports_unreadable_media(self):
        start = self.html.index("async function runVisualCaseSearch(file){")
        end = self.html.index("function renderMatchConflicts(m){", start)
        visual_code = self.html[start:end]
        script = r"""
const VISUAL_HASH_BITS = 63;
const VISUAL_HASH_MAX_DISTANCE = 12;
let visualSearchRequestId = 0;
async function imagePerceptualHash(blob) { return blob.hash; }
function imageHashDistance(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== 63 || b.length !== 63 || !/^[01]{63}$/.test(a) || !/^[01]{63}$/.test(b)) throw new Error("bad hash");
  let distance = 0;
  for (let i = 0; i < 63; i++) if (a[i] !== b[i]) distance++;
  return distance;
}
const zero = "0".repeat(63);
const hashes = {
  "a-weak.png": "1".repeat(9) + "0".repeat(54),
  "a-best.png": "1".repeat(2) + "0".repeat(61),
  "b.png": "1".repeat(12) + "0".repeat(51),
  "c.png": "1".repeat(13) + "0".repeat(50)
};
const status = { textContent: "" };
const box = { innerHTML: "", querySelectorAll() { return []; } };
function $(id) { return id === "visualSearchStatus" ? status : id === "visualSearchResults" ? box : null; }
function esc(value) { return String(value == null ? "" : value).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
function image() { return "<img>"; }
function openDetail() {}
const db = { cases: [
  { id: "case-a", status: "已验证成功", name: "案例A", sku: "A-1", images: ["a-weak.png", "a-best.png"], image: "a-best.png" },
  { id: "case-b", status: "已验证成功", name: "案例B", image: "b.png" },
  { id: "case-c", status: "已验证成功", name: "案例C", image: "c.png" },
  { id: "case-missing", status: "已验证成功", name: "缺图案例", image: "missing.png" },
  { id: "pending", status: "待审核", name: "未验证案例", image: "pending.png" }
]};
const fetched = [];
async function fetch(url) {
  const name = decodeURIComponent(url.split("/").pop());
  fetched.push(name);
  if (!(name in hashes)) return { ok: false };
  return { ok: true, blob: async () => ({ hash: hashes[name] }) };
}
"""
        script += visual_code
        script += r"""
(async () => {
  await runVisualCaseSearch({ type: "image/png", size: 10, hash: zero });
  if (fetched.length !== 5) throw new Error("Only verified cases should be searched and duplicate references within a case should be collapsed.");
  if (!box.innerHTML.includes('data-visual-case="case-a"') || !box.innerHTML.includes('data-visual-case="case-b"')) throw new Error("Cases at or below the Hamming threshold should be shown.");
  if (box.innerHTML.includes('data-visual-case="case-c"') || box.innerHTML.includes('data-visual-case="pending"')) throw new Error("Over-threshold or unverified cases must not be shown.");
  if ((box.innerHTML.match(/data-visual-case="case-a"/g) || []).length !== 1) throw new Error("Only the best matching image per case should be shown.");
  if (!box.innerHTML.includes("a-best.png") || box.innerHTML.includes("a-weak.png")) throw new Error("The best image within a case should be retained.");
  if (!status.textContent.includes("成功比较 4 张") || !status.textContent.includes("无法读取 1 张")) throw new Error("The result summary must distinguish compared and unreadable images.");
})().catch(error => { console.error(error); process.exit(1); });
"""
        self.run_node(script)

    def test_visual_search_caps_candidates_and_newer_search_wins(self):
        start = self.html.index("async function runVisualCaseSearch(file){")
        end = self.html.index("function renderMatchConflicts(m){", start)
        visual_code = self.html[start:end]
        script = r"""
const VISUAL_HASH_BITS = 63;
const VISUAL_HASH_MAX_DISTANCE = 12;
let visualSearchRequestId = 0;
async function imagePerceptualHash(blob) { return blob.hash; }
function imageHashDistance(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== 63 || b.length !== 63 || !/^[01]{63}$/.test(a) || !/^[01]{63}$/.test(b)) throw new Error("bad hash");
  let distance = 0;
  for (let i = 0; i < 63; i++) if (a[i] !== b[i]) distance++;
  return distance;
}
const zero = "0".repeat(63), ones = "1".repeat(63);
const status = { textContent: "" };
const box = { innerHTML: "", querySelectorAll() { return []; } };
function $(id) { return id === "visualSearchStatus" ? status : id === "visualSearchResults" ? box : null; }
function esc(value) { return String(value == null ? "" : value).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
function image() { return "<img>"; }
function openDetail() {}
let fetchCount = 0, firstFetchStarted;
const firstStarted = new Promise(resolve => { firstFetchStarted = resolve; });
let delayFirst = false;
async function fetch() {
  const call = ++fetchCount;
  if (delayFirst && call === 1) { firstFetchStarted(); await new Promise(resolve => setTimeout(resolve, 30)); }
  return { ok: true, blob: async () => ({ hash: ones }) };
}
"""
        script += visual_code
        script += r"""
(async () => {
  db = { cases: Array.from({ length: 501 }, (_, index) => ({ id: "bulk-" + index, status: "已验证成功", name: "批量案例" + index, image: "bulk-" + index + ".png" })) };
  await runVisualCaseSearch({ type: "image/png", size: 10, hash: zero });
  if (fetchCount !== 500) throw new Error("Visual search must respect the 500-image safety cap.");
  if (!status.textContent.includes("仅检索前500张")) throw new Error("The result must disclose when the library was truncated by the safety cap.");

  db = { cases: [{ id: "latest", status: "已验证成功", name: "最新请求案例", image: "latest.png" }] };
  fetchCount = 0; delayFirst = true;
  const first = runVisualCaseSearch({ type: "image/png", size: 10, hash: zero });
  await firstStarted;
  const second = runVisualCaseSearch({ type: "image/png", size: 10, hash: ones });
  await second;
  await first;
  if (!box.innerHTML.includes('data-visual-case="latest"') || status.textContent.includes("没有找到")) throw new Error("An older, slower search must not overwrite the newest search result.");
})().catch(error => { console.error(error); process.exit(1); });
"""
        self.run_node(script)


if __name__ == "__main__":
    unittest.main(verbosity=2)
