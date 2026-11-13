# -*- coding: utf-8 -*-
"""AI服装提示词资产工作台 V0.6.5。"""
import base64, hashlib, json, mimetypes, os, shutil, subprocess, sys, tempfile, threading, time, uuid, webbrowser
from pathlib import Path
from urllib.parse import unquote, quote, parse_qs, urlsplit
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from storage import Storage
from collector import scan_folder, sha256_file
from case_matcher import rank_matches, rank_case_matches
from ai_analyzer import analyze_pair, analyze_assets

APP_VERSION = "0.6.5"
GITHUB_OWNER = "Ricekillerr"
GITHUB_REPO = "ai-clothing-prompt-workbench"
GITHUB_BRANCH = "release/v0.6.5-stabilization"
RAW_BASE = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/"
MANIFEST_URL = RAW_BASE + "update_manifest.json"
ROOT = Path(__file__).resolve().parent
HOME = Path.home() / "AI_Prompt_Asset_Workbench"
IMAGES = HOME / "references"
DATA = HOME / "prompt_library.json"
HOME.mkdir(parents=True, exist_ok=True)
IMAGES.mkdir(parents=True, exist_ok=True)
ALLOWED_UPDATE_FILES = {"ai_prompt_workbench.py", "index.html", "启动AI服装提示词工作台.bat", "updater.py", "使用说明_AI服装提示词资产工作台_V0.4.3.txt", "使用说明_Prompt_Atelier_V1.0_候选版.txt", "storage.py", "collector.py", "case_matcher.py", "ai_analyzer.py"}
MAX_UPDATE_FILE_SIZE = 20 * 1024 * 1024

# Keep the legacy JSON intact; migrate it transactionally into SQLite on first launch.
LEGACY_DATA = DATA if DATA.exists() else next(
    (p for p in (ROOT / "prompt_library.json", ROOT / "V0.2_prompt_library.json") if p.exists()),
    DATA,
)
STORAGE = Storage(HOME, legacy_json=LEGACY_DATA)

AUTO_SCAN_INTERVAL_SECONDS = 60
AUTO_SCAN_WAKE = threading.Event()
AUTO_SCAN_STOP = threading.Event()
CLIENT_HEARTBEAT_LOCK = threading.Lock()
LAST_CLIENT_HEARTBEAT = 0.0
CLOSE_REQUESTED_AT = 0.0
CLIENT_HEARTBEAT_TIMEOUT_SECONDS = 15


def auto_scan_is_enabled():
    return STORAGE.get_setting("auto_scan_enabled", "0") == "1"

def auto_update_is_enabled():
    return STORAGE.get_setting("auto_update_enabled", "0") == "1"


def log_background_error(message):
    try:
        log = HOME / "background_tasks.log"
        with log.open("a", encoding="utf-8") as stream:
            stream.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + str(message) + "\n")
    except Exception:
        pass

def log_api_error(route, error):
    """Record route and exception diagnostics without logging request bodies or secrets."""
    try:
        log = HOME / "api_errors.log"
        code = getattr(error, "code", None) or getattr(error, "errno", None)
        message = type(error).__name__ + (f" code={code}" if code is not None else "")
        with log.open("a", encoding="utf-8") as stream:
            stream.write(time.strftime("[%Y-%m-%d %H:%M:%S] ")
                         + str(route)[:120] + " | " + message + "\n")
    except Exception:
        pass


def auto_scan_loop():
    while not AUTO_SCAN_STOP.is_set():
        AUTO_SCAN_WAKE.wait(AUTO_SCAN_INTERVAL_SECONDS)
        AUTO_SCAN_WAKE.clear()
        if AUTO_SCAN_STOP.is_set():
            break
        if not auto_scan_is_enabled():
            continue
        for source in STORAGE.list_scan_sources():
            if AUTO_SCAN_STOP.is_set() or not auto_scan_is_enabled():
                break
            try:
                folder = Path(source["folder_path"])
                if not folder.is_dir():
                    continue
                previous = STORAGE.get_scan_snapshot(source["id"])
                items = scan_folder(folder, bool(source["recursive"]), previous=previous)
                STORAGE.save_scan_results(source["id"], items)
            except Exception as exc:
                log_background_error(f"自动扫描失败 [{source.get('folder_path', '')}]：{exc}")


def heartbeat_watchdog(server):
    while not AUTO_SCAN_STOP.wait(1):
        now = time.monotonic()
        with CLIENT_HEARTBEAT_LOCK:
            last = LAST_CLIENT_HEARTBEAT
            close_requested = CLOSE_REQUESTED_AT
        should_close = bool(
            (last and now - last > CLIENT_HEARTBEAT_TIMEOUT_SECONDS)
            or (close_requested and now - close_requested > 2 and last < close_requested)
        )
        if should_close:
            threading.Thread(target=server.shutdown, daemon=True).start()
            return

def load_data():
    return STORAGE.load_data()

def save_data(d):
    STORAGE.save_data(d)


def _is_link_or_junction(path):
    if path.is_symlink():
        return True
    checker = getattr(path, "is_junction", None)
    try:
        return bool(checker()) if callable(checker) else False
    except OSError:
        return True


def _validated_ingest_file_path(record):
    source = next(
        (item for item in STORAGE.list_scan_sources() if item["id"] == record.get("source_id")),
        None,
    )
    if source is None:
        raise ValueError("采集文件对应的扫描来源不存在")
    source_path = Path(source["folder_path"])
    if _is_link_or_junction(source_path):
        raise ValueError("扫描来源已变为符号链接或目录联接，拒绝分析")
    root = source_path.resolve(strict=True)
    if os.path.normcase(os.path.normpath(str(root))) != os.path.normcase(
        os.path.normpath(str(source["folder_path"]))
    ):
        raise ValueError("扫描来源路径在登记后发生变化，拒绝分析")
    stored_path = Path(record["path"])
    if _is_link_or_junction(stored_path):
        raise ValueError("采集文件已变为符号链接或目录联接，拒绝分析")
    resolved = stored_path.resolve(strict=True)
    if os.path.normcase(os.path.normpath(str(resolved))) != os.path.normcase(
        os.path.normpath(str(record["path"]))
    ):
        raise ValueError("采集文件路径在登记后发生变化，拒绝分析")
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("采集文件已超出原扫描目录，拒绝分析") from exc
    if not resolved.is_file():
        raise ValueError("采集文件不存在或不再是普通文件")
    return resolved


def version_tuple(v):
    vals = []
    for part in str(v).strip().lstrip("vV").split("."):
        digits = "".join(c for c in part if c.isdigit())
        vals.append(int(digits or 0))
    return tuple((vals + [0, 0, 0])[:3])

def fetch_bytes(url, timeout=20):
    req = Request(url, headers={"User-Agent": "Prompt-Atelier-Updater/0.5.0", "Cache-Control": "no-cache"})
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

    def _validate_local_request(self, require_json=False):
        """Block DNS-rebinding Host headers and cross-origin browser writes to the loopback API."""
        allowed_hosts = {"127.0.0.1", "localhost"}
        expected_port = int(self.server.server_address[1])
        try:
            host = urlsplit("//" + str(self.headers.get("Host", "")))
            host_ok = (
                host.hostname in allowed_hosts
                and host.port == expected_port
                and not host.username and not host.password
            )
        except ValueError:
            host_ok = False
        if not host_ok:
            self.send_json(403, {"ok": False, "error": "拒绝非本机 Host 请求"})
            return False

        origin = self.headers.get("Origin")
        if origin:
            try:
                parsed_origin = urlsplit(origin)
                origin_ok = (
                    parsed_origin.scheme == "http"
                    and parsed_origin.hostname in allowed_hosts
                    and parsed_origin.port == expected_port
                    and not parsed_origin.username and not parsed_origin.password
                    and not parsed_origin.path and not parsed_origin.query
                    and not parsed_origin.fragment
                )
            except ValueError:
                origin_ok = False
            if not origin_ok:
                self.send_json(403, {"ok": False, "error": "拒绝跨来源请求"})
                return False

        if require_json:
            content_type = str(self.headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                self.send_json(415, {"ok": False, "error": "本地写入接口只接受 application/json"})
                return False
        return True

    def send_file_download(self, path, download_name):
        path = Path(path)
        size = path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(size))
        self.end_headers()
        with path.open("rb") as source:
            shutil.copyfileobj(source, self.wfile, length=1024 * 1024)
    def do_GET(self):
        if not self._validate_local_request():
            return
        path = unquote(self.path.split("?", 1)[0])
        if path in ("/", "/index.html"):
            f = ROOT / "index.html"
            self.send_bytes(200, f.read_bytes(), "text/html; charset=utf-8") if f.exists() else self.send_bytes(404, b"index.html missing", "text/plain")
        elif path == "/api/data":
            self.send_bytes(200, json.dumps(load_data(), ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
        elif path == "/api/version": self.send_json(200, {"version": APP_VERSION})
        elif path == "/api/settings/auto-update":
            self.send_json(200, {
                "enabled": STORAGE.get_setting("auto_update_enabled", "0") == "1",
                "idle_minutes": 15,
            })
        elif path == "/api/backup/full":
            try:
                archive = STORAGE.create_full_backup(IMAGES, app_version=APP_VERSION)
                self.send_file_download(archive, archive.name)
            except Exception as e:
                self.send_json(500, {"ok": False, "error": f"完整备份创建失败：{e}"})
        elif path == "/api/assets/revisions":
            params = parse_qs(urlsplit(self.path).query)
            asset_id = str((params.get("asset_id") or [""])[0]).strip()
            self.send_json(200, {"revisions": STORAGE.list_asset_revisions(asset_id)})
        elif path == "/api/assets/revision":
            params = parse_qs(urlsplit(self.path).query)
            asset_id = str((params.get("asset_id") or [""])[0]).strip()
            revision_id = str((params.get("revision_id") or [""])[0]).strip()
            self.send_json(200, STORAGE.get_asset_revision(asset_id, revision_id))
        elif path == "/api/cases/search":
            params = parse_qs(urlsplit(self.path).query)
            query = str((params.get("q") or [""])[0]).strip()
            threshold_raw = (params.get("threshold") or ["0.8"])[0]
            include_conflicts = (params.get("include_conflicts") or ["0"])[0].casefold() in {"1", "true", "yes"}
            if not query:
                self.send_json(200, {"matches": [], "threshold": 0.8, "message": "请输入款号或服装描述"})
            else:
                try:
                    threshold = min(1.0, max(0.0, float(threshold_raw)))
                except (TypeError, ValueError):
                    self.send_json(400, {"error": "threshold 必须是 0 到 1 之间的数字"})
                    return
                matches = rank_case_matches(
                    load_data().get("cases", []), query,
                    threshold=threshold, include_conflicts=include_conflicts,
                )
                self.send_json(200, {
                    "matches": matches,
                    "threshold": threshold,
                    "include_conflicts": include_conflicts,
                    "message": "冲突案例仅供人工复核" if include_conflicts else "",
                })
        elif path == "/api/analytics/retrieval":
            self.send_json(200, STORAGE.get_retrieval_analytics())
        elif path == "/api/scan/auto": self.send_json(200, {"enabled": auto_scan_is_enabled(), "interval_seconds": AUTO_SCAN_INTERVAL_SECONDS})
        elif path == "/api/scan/sources": self.send_json(200, {"sources": STORAGE.list_scan_sources()})
        elif path == "/api/scan/pending": self.send_json(200, {"items": STORAGE.list_pending_ingest()})
        elif path == "/api/scan/matches": self.send_json(200, {"matches": rank_matches(STORAGE.list_pending_ingest())})
        elif path == "/api/ai/reviews": self.send_json(200, {"reviews": STORAGE.list_ai_analyses("review")})
        elif path == "/api/update/auto":
            self.send_json(200, {
                "enabled": auto_update_is_enabled(),
                "skipped_version": STORAGE.get_setting("auto_update_skipped_version", "") or "",
            })
        elif path == "/api/update/check":
            try: self.send_json(200, check_remote_update())
            except Exception as e: self.send_json(200, {"ok": False, "current_version": APP_VERSION, "error": str(e)})
        elif path.startswith("/media/"):
            name = Path(path[len("/media/"):]).name; f = IMAGES / name
            if f.is_file(): self.send_bytes(200, f.read_bytes(), mimetypes.guess_type(str(f))[0] or "application/octet-stream")
            else: self.send_bytes(404, b"not found", "text/plain")
        else: self.send_bytes(404, b"not found", "text/plain")
    def do_POST(self):
        if not self._validate_local_request(require_json=self.path != "/api/backup/restore"):
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
            max_request = 500 * 1024 * 1024 if self.path == "/api/backup/restore" else 25 * 1024 * 1024
            if n <= 0 or n > max_request: raise ValueError("请求内容为空或超过大小限制")
            if self.path == "/api/backup/restore":
                if "application/zip" not in self.headers.get("Content-Type", "").lower():
                    raise ValueError("恢复文件必须是 ZIP 格式")
                temp_path = None
                try:
                    with tempfile.NamedTemporaryFile(prefix="restore_upload_", suffix=".zip",
                                                     dir=str(HOME), delete=False) as temp_file:
                        temp_path = Path(temp_file.name)
                        remaining = n
                        while remaining:
                            chunk = self.rfile.read(min(1024 * 1024, remaining))
                            if not chunk:
                                raise ValueError("恢复文件上传未完成")
                            temp_file.write(chunk)
                            remaining -= len(chunk)
                    result = STORAGE.restore_full_backup(temp_path)
                    self.send_json(200, result)
                finally:
                    if temp_path and temp_path.exists():
                        temp_path.unlink()
                return
            raw_body = self.rfile.read(n)
            d = json.loads(raw_body.decode("utf-8") or "{}")
            if self.path == "/api/settings/auto-update":
                enabled = d.get("enabled")
                if not isinstance(enabled, bool):
                    raise ValueError("自动更新设置必须是布尔值")
                STORAGE.set_setting("auto_update_enabled", "1" if enabled else "0")
                self.send_json(200, {"ok": True, "enabled": enabled})
            elif self.path == "/api/data":
                if not isinstance(d, dict): raise ValueError("数据格式不正确")
                d.setdefault("cases", []); d.setdefault("modules", []); save_data(d)
                self.send_json(200, {"ok": True})
            elif self.path == "/api/assets/revisions/restore":
                asset_id = str(d.get("asset_id", "")).strip()
                revision_id = str(d.get("revision_id", "")).strip()
                restored = STORAGE.restore_asset_revision(asset_id, revision_id)
                self.send_json(200, {"ok": True, "asset": restored, "data": load_data()})
            elif self.path == "/api/scan/auto":
                enabled = bool(d.get("enabled", False))
                STORAGE.set_setting("auto_scan_enabled", "1" if enabled else "0")
                if enabled:
                    AUTO_SCAN_WAKE.set()
                self.send_json(200, {"ok": True, "enabled": enabled, "interval_seconds": AUTO_SCAN_INTERVAL_SECONDS})
            elif self.path == "/api/client/heartbeat":
                global LAST_CLIENT_HEARTBEAT, CLOSE_REQUESTED_AT
                with CLIENT_HEARTBEAT_LOCK:
                    LAST_CLIENT_HEARTBEAT = time.monotonic()
                    CLOSE_REQUESTED_AT = 0.0
                self.send_json(200, {"ok": True})
            elif self.path == "/api/client/close":
                with CLIENT_HEARTBEAT_LOCK:
                    CLOSE_REQUESTED_AT = time.monotonic()
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
            elif self.path == "/api/cases/feedback":
                feedback_id = STORAGE.record_retrieval_feedback(
                    str(d.get("query", "")), str(d.get("case_id", "")),
                    d.get("score", 0), str(d.get("feedback", "")),
                )
                self.send_json(200, {"ok": True, "feedback_id": feedback_id})
            elif self.path == "/api/cases/outcome":
                result = STORAGE.record_generation_outcome(
                    str(d.get("case_id", "")), str(d.get("outcome", "")),
                    str(d.get("notes", "")),
                )
                self.send_json(200, {"ok": True, **result})
            elif self.path == "/api/ai/analyze":
                prompt_path = str(d.get("prompt_path", "")).strip()
                raw_paths = d.get("asset_paths")
                if raw_paths is None:
                    raw_paths = [d.get("asset_path", "")]
                if not isinstance(raw_paths, list) or not 1 <= len(raw_paths) <= 8:
                    raise ValueError("每次必须选择 1–8 张关联素材")
                asset_paths = [str(value).strip() for value in raw_paths]
                if any(not value for value in asset_paths) or len(set(asset_paths)) != len(asset_paths):
                    raise ValueError("关联素材路径为空或重复")
                prompt_record = STORAGE.get_ingest_record(prompt_path)
                if not prompt_record or prompt_record["media_type"] != "text":
                    raise ValueError("提示词文件必须来自已扫描的本地文件夹")
                prompt_file = _validated_ingest_file_path(prompt_record)
                asset_records = []
                asset_files = []
                for asset_path in asset_paths:
                    record = STORAGE.get_ingest_record(asset_path)
                    if not record or record["media_type"] not in {"image", "video"}:
                        raise ValueError("所有关联素材都必须来自已扫描的本地文件夹")
                    resolved_asset = _validated_ingest_file_path(record)
                    asset_records.append(record)
                    asset_files.append(resolved_asset)
                result = analyze_assets(
                    str(prompt_file), [str(path) for path in asset_files], str(d.get("api_key", "")),
                    str(d.get("endpoint", "https://api.openai.com/v1")),
                    str(d.get("model", "gpt-4o-mini")),
                    expected_prompt_sha256=prompt_record["sha256"],
                    expected_asset_sha256s=[record["sha256"] for record in asset_records],
                )
                result["asset_sha256s"] = [record["sha256"] for record in asset_records]
                analysis_id = STORAGE.save_ai_analysis(
                    str(prompt_file), str(asset_files[0]), prompt_record["sha256"], asset_records[0]["sha256"],
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
            elif self.path == "/api/update/auto":
                enabled = bool(d.get("enabled", False))
                STORAGE.set_setting("auto_update_enabled", "1" if enabled else "0")
                self.send_json(200, {"ok": True, "enabled": enabled})
            elif self.path == "/api/update/auto/skip":
                version = str(d.get("version", "")).strip()[:40]
                STORAGE.set_setting("auto_update_skipped_version", version)
                self.send_json(200, {"ok": True, "skipped_version": version})
            elif self.path == "/api/update/apply":
                # Preserve a verified snapshot of the user's database and local
                # reference images before any program update is staged or applied.
                pre_update_backup = STORAGE.create_full_backup(IMAGES, app_version=APP_VERSION)
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
                self.send_json(200, {"ok": True, "message": f"已下载并校验 V{latest}；更新前完整备份已保存为 {pre_update_backup.name}。工作台即将关闭并自动更新。"})
                threading.Timer(1.0, lambda: threading.Thread(target=self.server.shutdown, daemon=True).start()).start()
            else: self.send_json(404, {"ok": False, "error": "接口不存在"})
        except Exception as e:
            log_api_error(self.path, e)
            self.send_json(400, {"ok": False, "error": str(e)})

def main():
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    url = "http://127.0.0.1:8765"
    AUTO_SCAN_STOP.clear()
    if auto_scan_is_enabled():
        AUTO_SCAN_WAKE.set()
    threading.Thread(target=auto_scan_loop, name="PromptAtelierAutoScan", daemon=True).start()
    threading.Thread(target=heartbeat_watchdog, args=(server,), name="PromptAtelierHeartbeat", daemon=True).start()
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    print(f"Prompt Atelier V{APP_VERSION}：{url}")
    print("用户数据目录：" + str(HOME)); print("关闭工作台网页后，后台服务将在短暂等待后自动退出。")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        AUTO_SCAN_STOP.set()
        AUTO_SCAN_WAKE.set()
        server.server_close()

if __name__ == "__main__": main()
