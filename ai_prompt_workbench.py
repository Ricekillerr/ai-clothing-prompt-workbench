# -*- coding: utf-8 -*-
"""AI服装提示词资产工作台 V0.4.2。"""
import base64, hashlib, json, mimetypes, os, shutil, subprocess, sys, tempfile, threading, uuid, webbrowser
from pathlib import Path
from urllib.parse import unquote, quote
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from storage import Storage

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
ALLOWED_UPDATE_FILES = {"ai_prompt_workbench.py", "storage.py", "collector.py", "index.html", "启动AI服装提示词工作台.bat", "updater.py", "使用说明_AI服装提示词资产工作台_V0.4.2.txt"}
MAX_UPDATE_FILE_SIZE = 20 * 1024 * 1024

# Keep legacy JSON untouched; import it into SQLite on first launch.
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
                updater = ROOT / "updater.py"; launcher = ROOT / "启动AI服装提示词工作台.bat"
                if not updater.is_file() or not launcher.is_file():
                    shutil.rmtree(stage, ignore_errors=True)
                    raise ValueError("本地缺少 updater.py 或启动 BAT，请先安装完整修复包")
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
