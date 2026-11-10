# -*- coding: utf-8 -*-
"""AI服装提示词资产工作台 V0.4.3。"""
import base64, hashlib, json, mimetypes, os, shutil, subprocess, sys, tempfile, threading, uuid, webbrowser
from pathlib import Path
from urllib.parse import unquote, quote
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from storage import Storage
from collector import scan_folder, sha256_file
from case_matcher import rank_matches
from ai_analyzer import analyze_pair

APP_VERSION = "0.5.0"
GITHUB_OWNER = "Ricekillerr"
GITHUB_REPO = "ai-clothing-prompt-workbench"
GITHUB_BRANCH = "main"
RAW_BASE = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/"
MANIFEST_URL = RAW_BASE + "update_manifest.json"
ROOT = Path(__file__).resolve().parent
HOME = Path.home() / "AI_Prompt_Asset_Workbench"
IMAGES = HOME / "references"
DATA = HOME / "prompt_library.json"
HOME.mkdir(parents=True, exist_ok=True)
IMAGES.mkdir(parents=True, exist_ok=True)
ALLOWED_UPDATE_FILES = {"ai_prompt_workbench.py", "index.html", "启动AI服装提示词工作台.bat", "updater.py", "使用说明_AI服装提示词资产工作台_V0.4.3.txt", "storage.py", "collector.py", "case_matcher.py", "ai_analyzer.py"}
MAX_UPDATE_FILE_SIZE = 20 * 1024 * 1024

# Keep the legacy JSON intact; migrate it transactionally into SQLite on first launch.
LEGACY_DATA = DATA if DATA.exists() else next(
    (p for p in (ROOT / "prompt_library.json", ROOT / "V0.2_prompt_library.json") if p.exists()),
    DATA,
)
STORAGE = Storage(HOME, legacy_json=LEGACY_DATA)

def load_data():
    return STORAGE.load_data()

def save_data(d):
    STORAGE.save_data(d)

def version_tuple(v):
    vals = []
    for part in str(v).strip().lstrip("vV").split("."):
        digits = "".join(c for c in part if c.isdigit())
        vals.append(int(digits or 0))
    return tuple((vals + [0, 0, 0])[:3])

def fetch_bytes(url, timeout=20):
    req = Request(url, headers={"User-Agent": "Prompt-Atelier-Updater/0.4.2", "Cache-Control": "no-cache"})
    with urlopen(req, timeout=timeout) as res:
        if getattr(res, "status", 200) != 200:
            raise ValueError(f"下载服务器返回状态码：{res.status}")
        return res.read()

def fetch_manifest():
    raw = fetch_bytes(MANIFEST_URL)
    manifest = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(manifest, dict) or not manifest.get("version"):
        raise ValueError("GitHub 更新清单格式不正确")
    return manifest

def check_remote_update():
    manifest = fetch_manifest()
    latest = str(manifest["version"]).strip()
    return {"ok": True, "current_version": APP_VERSION, "latest_version": latest,
            "update_available": version_tuple(latest) > version_tuple(APP_VERSION),
            "notes": str(manifest.get("notes", ""))}

def stage_update():
    manifest = fetch_manifest()
    latest = str(manifest["version"]).strip()
    if version_tuple(latest) <= version_tuple(APP_VERSION):
        raise ValueError("当前已是最新版本")
    files = manifest.get("files")
    hashes = manifest.get("sha256")
    if not isinstance(files, list) or not files:
        raise ValueError("更新清单 files 必须是非空数组")
    if not isinstance(hashes, dict):
        raise ValueError("更新清单缺少 SHA-256 完整性校验信息")
    files = list(dict.fromkeys(files))
    bad = [str(n) for n in files if not isinstance(n, str) or n not in ALLOWED_UPDATE_FILES]
    if bad:
        raise ValueError("更新清单包含不允许更新的文件：" + ", ".join(bad))
    missing = [n for n in files if n not in hashes]
    if missing:
        raise ValueError("以下文件缺少 SHA-256 校验值：" + ", ".join(missing))
    stage = Path(tempfile.mkdtemp(prefix="prompt_atelier_update_"))
    try:
        verified = {}
        for name in files:
            expected = str(hashes[name]).lower()
            if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
                raise ValueError("更新清单中的 SHA-256 格式无效：" + name)
            data = fetch_bytes(RAW_BASE + quote(name), timeout=45)
            if not data:
                raise ValueError("下载文件为空：" + name)
            if len(data) > MAX_UPDATE_FILE_SIZE:
                raise ValueError("更新文件超过 20MB 安全限制：" + name)
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError("文件完整性校验失败，已取消更新：" + name)
            (stage / name).write_bytes(data)
            verified[name] = expected
        (stage / "update_meta.json").write_text(json.dumps({"version": latest, "files": verified}, ensure_ascii=False, indent=2), encoding="utf-8")
        return stage, latest
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def send_bytes(self, code, data, ctype):
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)
    def send_json(self, code, obj):
        self.send_bytes(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
    def do_GET(self):
        path = unquote(self.path.split("?", 1)[0])
        if path in ("/", "/index.html"):
            f = ROOT / "index.html"
            self.send_bytes(200, f.read_bytes(), "text/html; charset=utf-8") if f.exists() else self.send_bytes(404, b"index.html missing", "text/plain")
        elif path == "/api/data":
            self.send_bytes(200, json.dumps(load_data(), ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
        elif path == "/api/version": self.send_json(200, {"version": APP_VERSION})
        elif path == "/api/scan/sources": self.send_json(200, {"sources": STORAGE.list_scan_sources()})
        elif path == "/api/scan/pending": self.send_json(200, {"items": STORAGE.list_pending_ingest()})
        elif path == "/api/scan/matches": self.send_json(200, {"matches": rank_matches(STORAGE.list_pending_ingest())})
        elif path == "/api/ai/reviews": self.send_json(200, {"reviews": STORAGE.list_ai_analyses("review")})
        elif path == "/api/update/check":
            try: self.send_json(200, check_remote_update())
            except Exception as e: self.send_json(200, {"ok": False, "current_version": APP_VERSION, "error": str(e)})
        elif path.startswith("/media/"):
            name = Path(path[len("/media/"):]).name; f = IMAGES / name
            if f.is_file(): self.send_bytes(200, f.read_bytes(), mimetypes.guess_type(str(f))[0] or "application/octet-stream")
            else: self.send_bytes(404, b"not found", "text/plain")
        else: self.send_bytes(404, b"not found", "text/plain")
    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if n > 25 * 1024 * 1024: raise ValueError("请求内容过大")
            d = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            if self.path == "/api/data":
                if not isinstance(d, dict): raise ValueError("数据格式不正确")
                d.setdefault("cases", []); d.setdefault("modules", []); save_data(d)
                self.send_json(200, {"ok": True})
            elif self.path == "/api/scan/source":
                folder = str(d.get("folder", "")).strip()
                if not folder: raise ValueError("请提供需要扫描的文件夹路径")
                source_id = STORAGE.register_scan_source(folder, bool(d.get("recursive", True)))
                self.send_json(200, {"ok": True, "source_id": source_id})
            elif self.path == "/api/scan/run":
                source_id = str(d.get("source_id", "")).strip()
                source = next((x for x in STORAGE.list_scan_sources() if x["id"] == source_id), None)
                if not source: raise ValueError("扫描来源不存在")
                previous = STORAGE.get_scan_snapshot(source_id)
                items = scan_folder(Path(source["folder_path"]), bool(source["recursive"]), previous=previous)
                summary = STORAGE.save_scan_results(source_id, items)
                self.send_json(200, {"ok": True, "summary": summary})
            elif self.path == "/api/ai/analyze":
                prompt_path = str(d.get("prompt_path", "")).strip()
                asset_path = str(d.get("asset_path", "")).strip()
                prompt_record = STORAGE.get_ingest_record(prompt_path)
                asset_record = STORAGE.get_ingest_record(asset_path)
                if not prompt_record or prompt_record["media_type"] != "text":
                    raise ValueError("提示词文件必须来自已扫描的本地文件夹")
                if not asset_record or asset_record["media_type"] not in {"image", "video"}:
                    raise ValueError("关联素材必须来自已扫描的本地文件夹")
                if sha256_file(Path(prompt_path)) != prompt_record["sha256"]:
                    raise ValueError("提示词文件在扫描后发生变化，请重新扫描再分析")
                if sha256_file(Path(asset_path)) != asset_record["sha256"]:
                    raise ValueError("关联素材在扫描后发生变化，请重新扫描再分析")
                result = analyze_pair(
                    prompt_path, asset_path, str(d.get("api_key", "")),
                    str(d.get("endpoint", "https://api.openai.com/v1")),
                    str(d.get("model", "gpt-4o-mini")),
                )
                analysis_id = STORAGE.save_ai_analysis(
                    prompt_path, asset_path, prompt_record["sha256"], asset_record["sha256"],
                    str(result.get("model", "")), result,
                )
                self.send_json(200, {"ok": True, "analysis_id": analysis_id, "result": result})
            elif self.path == "/api/ai/approve":
                asset_id = STORAGE.approve_ai_analysis(str(d.get("analysis_id", "")))
                self.send_json(200, {"ok": True, "asset_id": asset_id, "data": load_data()})
            elif self.path == "/api/ai/reject":
                STORAGE.reject_ai_analysis(str(d.get("analysis_id", "")))
                self.send_json(200, {"ok": True})
            elif self.path == "/api/scan/review":
                file_path = str(d.get("path", "")).strip()
                status = str(d.get("status", "")).strip()
                if not file_path: raise ValueError("缺少待审核文件路径")
                STORAGE.set_ingest_status(file_path, status)
                self.send_json(200, {"ok": True})
            elif self.path == "/api/image":
                ext = Path(d.get("name", "image.png")).suffix.lower()
                if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"): ext = ".png"
                content = base64.b64decode(d.get("data", "").split(",", 1)[-1], validate=True)
                if not content: raise ValueError("图片内容为空")
                if len(content) > 15 * 1024 * 1024: raise ValueError("图片不能超过15MB")
                name = uuid.uuid4().hex[:16] + ext; (IMAGES / name).write_bytes(content)
                self.send_json(200, {"path": name})
            elif self.path == "/api/update/apply":
                stage, latest = stage_update()
                # Bootstrap from the verified staged updater. Older installs may not
                # contain updater.py or the launcher yet; both are included in the
                # manifest and installed by the updater transaction.
                updater = stage / "updater.py"
                launcher = ROOT / "启动AI服装提示词工作台.bat"
                if not updater.is_file():
                    shutil.rmtree(stage, ignore_errors=True)
                    raise ValueError("更新包缺少经过校验的 updater.py，已安全取消更新")
                flags = 0
                if sys.platform == "win32": flags = getattr(subprocess, "DETACHED_PROCESS", 8) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 512)
                subprocess.Popen([sys.executable, str(updater), str(stage), str(ROOT), str(launcher)], cwd=str(ROOT),
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=flags, close_fds=(sys.platform != "win32"))
                self.send_json(200, {"ok": True, "message": f"已下载并校验 V{latest}，工作台即将关闭并自动更新。"})
                threading.Timer(1.0, lambda: threading.Thread(target=self.server.shutdown, daemon=True).start()).start()
            else: self.send_json(404, {"ok": False, "error": "接口不存在"})
        except Exception as e: self.send_json(400, {"ok": False, "error": str(e)})

def main():
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    url = "http://127.0.0.1:8765"
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    print(f"Prompt Atelier V{APP_VERSION}：{url}")
    print("用户数据目录：" + str(HOME)); print("关闭此窗口即可停止工作台。")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__ == "__main__": main()
