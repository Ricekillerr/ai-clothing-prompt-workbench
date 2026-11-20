# -*- coding: utf-8 -*-
"""Prompt Atelier V0.5.0 local-first storage layer.

SQLite stores structured records. Original JSON is retained as a migration
source and is never overwritten by the migration process.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import threading
import uuid
import zipfile
import warnings
from contextlib import contextmanager
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSION = 1
MAX_SUPPORTED_SCHEMA_VERSION = SCHEMA_VERSION


def _is_link_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    checker = getattr(path, "is_junction", None)
    try:
        return bool(checker()) if callable(checker) else False
    except OSError:
        return True


MAX_IMAGE_BYTES = 100 * 1024 * 1024
MAX_IMAGE_PIXELS = 80_000_000
MAX_VIDEO_BYTES = 1024 * 1024 * 1024
VIDEO_FORMATS = {".mp4", ".mov", ".m4v", ".webm", ".avi"}
FORMATS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP",
           ".gif": "GIF", ".bmp": "BMP"}


def _link(path):
    try:
        return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())
    except OSError:
        return True


def _is_legacy_absolute_video(item):
    """An old absolute video reference can only be retained as unresolved metadata."""
    path = item.get("relative_path")
    return (item.get("media_type") == "video" and item.get("status") == "unsafe_path"
            and isinstance(path, str) and (PureWindowsPath(path).is_absolute() or PurePosixPath(path).is_absolute()))


def _blocking_preflight_items(report):
    pending = set(report.get("unresolved_video_ids", []))
    return [item for item in report["attachments"]
            if item["status"] != "verified" and not (
                _is_legacy_absolute_video(item)
                and (str(report["schema_version"]) == "1" or item["id"] in pending))] + [
                    item for item in report["payload_references"] if item["status"] != "verified"]


def _verify_video_file(path: Path) -> str:
    """Fully decode a local video; fail closed if the decoder is unavailable."""
    try:
        import imageio_ffmpeg
        binary = imageio_ffmpeg.get_ffmpeg_exe()
        with path.open("rb") as stream:
            header = stream.read(16)
        ext = path.suffix.lower()
        if ext in {".mp4", ".mov", ".m4v"}:
            matches = len(header) >= 12 and header[4:8] in {b"ftyp", b"moov", b"wide"}
        elif ext == ".webm":
            matches = header.startswith(b"\x1a\x45\xdf\xa3")
        elif ext == ".avi":
            matches = header[:4] == b"RIFF" and header[8:12] == b"AVI "
        else:
            return "unsupported"
        if not matches:
            return "corrupt"
        result = subprocess.run(
            [binary, "-v", "error", "-xerror", "-nostdin", "-protocol_whitelist", "file,pipe",
             "-i", str(path), "-map", "0:v:0", "-f", "null", "-"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=120, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return "verified" if result.returncode == 0 else "corrupt"
    except (ImportError, OSError, subprocess.TimeoutExpired):
        return "unverified"


def _attachment_status(root, relative_path, media_type, expected, computed=None,
                       allow_missing_hash=False):
    path = Path(relative_path)
    windows = PureWindowsPath(relative_path)
    if (not relative_path or path.is_absolute() or windows.is_absolute() or windows.drive
            or ".." in path.parts or ".." in windows.parts or "\\" in relative_path):
        return "unsafe_path"
    current = root
    if any(_link(component) for component in (root, *root.parents)):
        return "unsafe_path"
    for component in path.parts:
        current = current / component
        if _link(current):
            return "unsafe_path"
    try:
        if not current.resolve().is_relative_to(root.resolve()):
            return "unsafe_path"
        if not current.is_file():
            return "missing"
        image_format = FORMATS.get(current.suffix.lower())
        if not ((media_type == "image" and image_format is not None)
                or (media_type == "video" and current.suffix.lower() in VIDEO_FORMATS)):
            return "unsupported"
        initial = current.stat()
        if initial.st_size <= 0 or initial.st_size > (MAX_VIDEO_BYTES if media_type == "video" else MAX_IMAGE_BYTES):
            return "corrupt"
        digest = hashlib.sha256()
        with current.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        actual_hash = digest.hexdigest()
        if computed is not None:
            computed["sha256"] = actual_hash
        if expected and actual_hash != str(expected).lower():
            return "hash_mismatch"
        if media_type == "video":
            status = _verify_video_file(current)
            final = current.stat()
            if (initial.st_size, initial.st_mtime_ns, initial.st_ino) != (final.st_size, final.st_mtime_ns, final.st_ino):
                return "unverified"
            return status if expected or allow_missing_hash else "unverified"
        try:
            from PIL import Image, UnidentifiedImageError
        except ImportError:
            return "unverified"
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(current) as image:
                    if image.format != image_format:
                        return "corrupt"
                    if image.width <= 0 or image.height <= 0 or image.width * image.height > MAX_IMAGE_PIXELS:
                        return "corrupt"
                    image.verify()
                with Image.open(current) as image:
                    for frame in range(getattr(image, "n_frames", 1)):
                        image.seek(frame)
                        image.load()
        except (OSError, ValueError, SyntaxError, EOFError, Image.DecompressionBombError,
                Image.DecompressionBombWarning, UnidentifiedImageError):
            return "corrupt"
        final = current.stat()
        if (initial.st_size, initial.st_mtime_ns, initial.st_ino) != (final.st_size, final.st_mtime_ns, final.st_ino):
            return "unverified"
        return "verified" if expected or allow_missing_hash else "unverified"
    except PermissionError:
        return "unverified"
    except OSError:
        return "unverified"



class StorageError(RuntimeError):
    pass


@contextmanager
def _activation_lock(source_home):
    """Hold an OS-released lock across protected-copy activation or recovery."""
    path = Path(source_home) / ".stage2-activation.lock"
    if _is_link_or_junction(path):
        raise StorageError("迁移锁不能是符号链接或目录联接")
    with path.open("a+b") as stream:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise StorageError("已有迁移或恢复任务正在运行") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def active_data_home(profile: Path) -> Path:
    """Select only a fully initialized isolated V2 store."""
    profile = Path(profile)
    legacy = profile / "AI_Prompt_Asset_Workbench"
    container = profile / "AI_Prompt_Asset_Workbench_v2"
    if not container.exists() and not _is_link_or_junction(container):
        return legacy
    if _is_link_or_junction(container):
        raise StorageError("V2 数据目录不能是符号链接或目录联接")
    marker = container / "stage2-ready.json"
    data = container / "data"
    if not marker.exists() and not _is_link_or_junction(marker):
        # Recover only a uniquely identified, verified staging copy.
        if data.is_dir() and not _is_link_or_junction(data):
            stage = profile / (".stage2-copy-moved-" + uuid.uuid4().hex)
        else:
            matching = []
            for stage in profile.glob(".stage2-copy-*"):
                origin = stage / "stage2-origin.json"
                if _is_link_or_junction(stage) or _is_link_or_junction(origin):
                    continue
                try:
                    if origin.is_file() and origin.stat().st_size <= 16384:
                        record = json.loads(origin.read_text(encoding="utf-8"))
                        if Path(record.get("destination", "")) == container.resolve():
                            matching.append(stage)
                except (OSError, ValueError, TypeError):
                    continue
            if len(matching) != 1:
                raise StorageError("V2 迁移未完成，无法安全确定暂存副本")
            stage = matching[0]
        recover_protected_copy(container, stage)
    database = data / "prompt_library.sqlite3"
    if (_is_link_or_junction(marker) or _is_link_or_junction(data)
            or _is_link_or_junction(database) or not marker.is_file()
            or not database.is_file()):
        raise StorageError("V2 数据目录尚未安全完成，请保留文件并恢复")
    try:
        settings = json.loads(marker.read_text(encoding="utf-8"))
        if settings != {"schema_version": 2}:
            raise StorageError("V2 数据目录标记无效")
        con = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            version = con.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone()
            if version is None or str(version[0]) != "2":
                raise StorageError("V2 数据库版本无效")
            names = {row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            required = {"assets", "attachments", "case_tombstones", "garments", "garment_revisions",
                        "case_garments", "prompt_sources", "prompt_segments",
                        "prompt_segment_refs", "ps_notes", "ps_note_assets",
                        "garment_candidates", "migration_review"}
            if not required.issubset(names) or con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise StorageError("V2 数据库结构或完整性检查失败")
        finally:
            con.close()
    except (OSError, ValueError, sqlite3.Error) as exc:
        raise StorageError("V2 数据目录无法安全读取") from exc
    return data


def legacy_divergence_status(profile: Path) -> dict:
    """Compare the retained V1 source with its migration snapshot, without writing either store."""
    profile = Path(profile)
    legacy = profile / "AI_Prompt_Asset_Workbench"
    container = profile / "AI_Prompt_Asset_Workbench_v2"
    origin_file = container / "data" / "stage2-origin.json"
    if not container.exists():
        return {"status": "not_migrated"}
    try:
        if any(_is_link_or_junction(p) for p in (container, container / "data", origin_file, legacy)):
            return {"status": "unverified", "message": "旧版数据路径不安全，无法核对"}
        if not origin_file.is_file() or origin_file.stat().st_size > 4 * 1024 * 1024:
            return {"status": "unverified", "message": "迁移来源记录不可读取"}
        origin = json.loads(origin_file.read_text(encoding="utf-8"))
        if Path(origin["source_home"]).resolve() != legacy.resolve():
            return {"status": "unverified", "message": "迁移来源路径不一致"}
        report = inspect_database(legacy / "prompt_library.sqlite3", legacy / "references")
        if report["schema_version"] != "1" or report["schema_errors"] or report["foreign_key_errors"] or report["integrity"] != ["ok"]:
            return {"status": "unverified", "message": "旧版数据完整性无法确认"}
        if (report["legacy_fingerprint"] != origin["legacy_fingerprint"]
                or report["attachments"] != origin["attachments"]
                or report["payload_references"] != origin["payload_references"]):
            return {"status": "changed", "message": "迁移后旧版数据发生变化。两份数据均已保留，请勿使用旧版覆盖新版。"}
        return {"status": "unchanged"}
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        return {"status": "unverified", "message": "无法核对旧版数据；请保留两份数据"}


class Storage:
    def __init__(self, home: Path, legacy_json: Path | None = None):
        self._db_lock = threading.RLock()
        self._operation_condition = threading.Condition(threading.RLock())
        self._active_operations = 0
        self._active_by_thread = {}
        self._restoring = False
        self._maintenance_owner = None
        self._maintenance_depth = 0
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.db_path = self.home / "prompt_library.sqlite3"
        self.legacy_json = Path(legacy_json) if legacy_json else self.home / "prompt_library.json"
        self._check_existing_schema()
        self._initialize()

    def _check_existing_schema(self):
        """Reject unknown schema versions before any writable connection."""
        if not self.db_path.is_file():
            return
        try:
            con = sqlite3.connect(self.db_path.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                metadata = con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'"
                ).fetchone()
                if not metadata:
                    raise StorageError("现有数据库缺少版本信息")
                row = con.execute(
                    "SELECT value FROM metadata WHERE key='schema_version'"
                ).fetchone()
                if row is None or not str(row[0]).isdigit():
                    raise StorageError("现有数据库版本无效")
                if int(row[0]) > MAX_SUPPORTED_SCHEMA_VERSION or int(row[0]) < 1:
                    raise StorageError("数据库版本不受此程序支持，已拒绝写入")
            finally:
                con.close()
        except sqlite3.Error as exc:
            raise StorageError("无法安全读取现有数据库版本") from exc

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=15)
        con.row_factory = sqlite3.Row
        con.create_function("prompt_atelier_stage2_write", 0, lambda: 1)
        con.create_function("prompt_atelier_sha256", 1,
                            lambda text: hashlib.sha256(text.encode("utf-8")).hexdigest())
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
        return con

    @contextmanager
    def operation(self):
        """Lease a normal operation; multiple leases may overlap, restore may not."""
        owner = threading.get_ident()
        condition = self._operation_condition
        with condition:
            while (
                self._restoring
                and self._maintenance_owner != owner
                and self._active_by_thread.get(owner, 0) == 0
            ):
                condition.wait()
            self._active_operations += 1
            self._active_by_thread[owner] = self._active_by_thread.get(owner, 0) + 1
        try:
            yield
        finally:
            with condition:
                self._active_operations -= 1
                remaining = self._active_by_thread.get(owner, 0) - 1
                if remaining > 0:
                    self._active_by_thread[owner] = remaining
                else:
                    self._active_by_thread.pop(owner, None)
                condition.notify_all()

    def is_exclusive_operation_active(self) -> bool:
        """Expose restore state to lifecycle services that must remain responsive."""
        with self._operation_condition:
            return self._restoring

    @contextmanager
    def exclusive_operation(self):
        """Block new operations and wait for in-flight work before replacing local state."""
        owner = threading.get_ident()
        condition = self._operation_condition
        with condition:
            if self._restoring and self._maintenance_owner == owner:
                self._maintenance_depth += 1
            else:
                while self._restoring:
                    condition.wait()
                self._restoring = True
                self._maintenance_owner = owner
                self._maintenance_depth = 1
                try:
                    # Existing operations may finish nested storage calls while
                    # this exclusive operation waits for their outer lease to end.
                    while self._active_operations > self._active_by_thread.get(owner, 0):
                        condition.wait()
                except BaseException:
                    self._restoring = False
                    self._maintenance_owner = None
                    self._maintenance_depth = 0
                    condition.notify_all()
                    raise
        try:
            yield
        finally:
            with condition:
                self._maintenance_depth -= 1
                if self._maintenance_depth <= 0:
                    self._restoring = False
                    self._maintenance_owner = None
                    self._maintenance_depth = 0
                    condition.notify_all()

    @contextmanager
    def _session(self):
        with self.operation():
            with self._db_lock:
                con = self._connect()
                try:
                    yield con
                    con.commit()
                except Exception:
                    con.rollback()
                    raise
                finally:
                    con.close()

    def _initialize(self) -> None:
        existed = self.db_path.exists()
        # Validate the complete legacy file before creating a new database.
        # A malformed source must never leave behind an empty DB that masks it
        # on the next launch.
        if not existed and self.legacy_json.exists():
            self._read_legacy(self.legacy_json)
        try:
            with self._session() as con:
                con.executescript("""
                    CREATE TABLE IF NOT EXISTS metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS assets (
                        id TEXT PRIMARY KEY,
                        kind TEXT NOT NULL CHECK(kind IN ('case','module')),
                        payload_json TEXT NOT NULL,
                        created_at TEXT,
                        updated_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_assets_kind ON assets(kind);
                    CREATE INDEX IF NOT EXISTS idx_assets_updated ON assets(updated_at);
                    CREATE TABLE IF NOT EXISTS asset_revisions (
                        revision_id TEXT PRIMARY KEY,
                        asset_id TEXT NOT NULL,
                        kind TEXT NOT NULL CHECK(kind IN ('case','module')),
                        payload_json TEXT NOT NULL,
                        reason TEXT NOT NULL DEFAULT 'edited',
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_asset_revisions_asset
                        ON asset_revisions(asset_id, created_at DESC);
                    CREATE TABLE IF NOT EXISTS attachments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                        relative_path TEXT NOT NULL,
                        media_type TEXT NOT NULL DEFAULT 'image',
                        role TEXT NOT NULL DEFAULT 'reference',
                        sha256 TEXT,
                        created_at TEXT,
                        UNIQUE(asset_id, relative_path, role)
                    );
                    CREATE INDEX IF NOT EXISTS idx_attachments_asset ON attachments(asset_id);
                    CREATE TABLE IF NOT EXISTS scan_sources (
                        id TEXT PRIMARY KEY,
                        folder_path TEXT NOT NULL UNIQUE,
                        recursive INTEGER NOT NULL DEFAULT 1,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        last_scanned_at TEXT
                    );
                    CREATE TABLE IF NOT EXISTS ingest_files (
                        path TEXT PRIMARY KEY,
                        source_id TEXT NOT NULL REFERENCES scan_sources(id) ON DELETE CASCADE,
                        relative_path TEXT NOT NULL,
                        name TEXT NOT NULL,
                        extension TEXT NOT NULL,
                        media_type TEXT NOT NULL,
                        size INTEGER NOT NULL,
                        modified_ns INTEGER NOT NULL,
                        sha256 TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending'
                            CHECK(status IN ('pending','approved','ignored','missing')),
                        first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE INDEX IF NOT EXISTS idx_ingest_status ON ingest_files(status);
                    CREATE INDEX IF NOT EXISTS idx_ingest_source ON ingest_files(source_id);
                    CREATE TABLE IF NOT EXISTS generation_feedback (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        case_id TEXT NOT NULL,
                        outcome TEXT NOT NULL CHECK(outcome IN ('success','failed')),
                        notes TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_generation_feedback_case
                        ON generation_feedback(case_id, created_at DESC);
                    CREATE TABLE IF NOT EXISTS retrieval_feedback (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        query_text TEXT NOT NULL,
                        case_id TEXT NOT NULL,
                        score REAL NOT NULL,
                        feedback TEXT NOT NULL CHECK(feedback IN ('useful','not_relevant')),
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_retrieval_feedback_created
                        ON retrieval_feedback(created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_retrieval_feedback_case
                        ON retrieval_feedback(case_id, feedback);
                    CREATE TABLE IF NOT EXISTS ai_analyses (
                        id TEXT PRIMARY KEY,
                        prompt_path TEXT NOT NULL,
                        asset_path TEXT NOT NULL,
                        prompt_sha256 TEXT NOT NULL,
                        asset_sha256 TEXT NOT NULL,
                        model TEXT NOT NULL,
                        result_json TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'review'
                            CHECK(status IN ('review','approved','rejected')),
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        approved_asset_id TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_ai_analyses_status ON ai_analyses(status);
                """)
                con.execute(
                    "INSERT OR IGNORE INTO metadata(key,value) VALUES('schema_version',?)",
                    (str(LEGACY_SCHEMA_VERSION),),
                )
                row = con.execute("PRAGMA quick_check").fetchone()
                if not row or row[0] != "ok":
                    raise StorageError("SQLite 数据库完整性检查失败，已停止写入以保护数据。")
        except Exception as exc:
            if existed:
                raise StorageError(
                    f"现有数据库无法安全打开：{exc}。原文件未删除或覆盖；请先恢复备份。"
                ) from exc
            raise StorageError(f"初始化本地数据库失败：{exc}") from exc

        # Resume an interrupted first-run migration. The marker is committed
        # in the same transaction as imported records, so absence means retry.
        if self.legacy_json.exists():
            with self._session() as con:
                marker = con.execute(
                    "SELECT value FROM metadata WHERE key='legacy_migration_complete'"
                ).fetchone()
            if marker is None:
                self.import_legacy_json(self.legacy_json)
        else:
            with self._session() as con:
                con.execute(
                    "INSERT OR IGNORE INTO metadata(key,value) VALUES('legacy_migration_complete','no_legacy_source')"
                )

    @staticmethod
    def _read_legacy(path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            raise StorageError(
                f"旧 JSON 文件无法解析：{path}。为避免数据丢失，未创建空库替代它。"
            ) from exc
        if not isinstance(data, dict):
            raise StorageError("旧 JSON 根节点必须是对象；原文件保持不变。")
        seen_ids: set[str] = set()
        for key in ("cases", "modules"):
            if key not in data or not isinstance(data[key], list):
                raise StorageError(f"旧 JSON 缺少有效的 {key} 数组；原文件保持不变。")
            if any(not isinstance(item, dict) for item in data[key]):
                raise StorageError(f"旧 JSON 的 {key} 中存在非对象记录；原文件保持不变。")
            for item in data[key]:
                asset_id = str(item.get("id") or "").strip()
                if not asset_id:
                    raise StorageError(f"旧 JSON 的 {key} 中存在没有 id 的记录；原文件保持不变。")
                if asset_id in seen_ids:
                    raise StorageError(f"旧 JSON 存在重复 id：{asset_id}；原文件保持不变。")
                seen_ids.add(asset_id)
        return data

    def import_legacy_json(self, path: Path | None = None) -> dict[str, int]:
        source = Path(path) if path else self.legacy_json
        data = self._read_legacy(source)
        counts = {"cases": 0, "modules": 0}
        try:
            with self._session() as con:
                # Import is all-or-nothing. Existing IDs are not overwritten.
                for key, kind in (("cases", "case"), ("modules", "module")):
                    for item in data[key]:
                        asset_id = str(item["id"]).strip()
                        cur = con.execute(
                            "INSERT OR IGNORE INTO assets(id,kind,payload_json,created_at,updated_at) "
                            "VALUES(?,?,?,?,?)",
                            (asset_id, kind, json.dumps(item, ensure_ascii=False),
                             item.get("created_at"), item.get("updated_at")),
                        )
                        counts[key] += cur.rowcount
                actual = {
                    "cases": con.execute("SELECT COUNT(*) FROM assets WHERE kind='case'").fetchone()[0],
                    "modules": con.execute("SELECT COUNT(*) FROM assets WHERE kind='module'").fetchone()[0],
                }
                expected = {"cases": len(data["cases"]), "modules": len(data["modules"])}
                if actual != expected:
                    raise StorageError(
                        f"迁移数量核对失败：源数据 {expected}，数据库 {actual}；已回滚迁移。"
                    )
                con.execute(
                    "INSERT OR REPLACE INTO metadata(key,value) VALUES('legacy_import_source',?)",
                    (str(source),),
                )
                con.execute(
                    "INSERT OR REPLACE INTO metadata(key,value) VALUES('legacy_migration_complete','true')"
                )
        except Exception as exc:
            if isinstance(exc, StorageError):
                raise
            raise StorageError(f"JSON 迁移失败，事务已回滚：{exc}") from exc
        return counts

    def load_data(self) -> dict[str, Any]:
        try:
            with self._session() as con:
                has_tombstones = con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='case_tombstones'"
                ).fetchone() is not None
                where = " WHERE id NOT IN (SELECT case_id FROM case_tombstones)" if has_tombstones else ""
                rows = con.execute(
                    "SELECT kind,payload_json FROM assets" + where + " ORDER BY updated_at DESC, rowid DESC"
                ).fetchall()
                result: dict[str, Any] = {"version": 3, "cases": [], "modules": []}
                for row in rows:
                    item = json.loads(row["payload_json"])
                    result["cases" if row["kind"] == "case" else "modules"].append(item)
                return result
        except Exception as exc:
            raise StorageError(f"读取数据库失败；为保护原数据，已拒绝返回空数据：{exc}") from exc

    def save_data(self, data: dict[str, Any]) -> None:
        if not isinstance(data, dict) or not isinstance(data.get("cases"), list) or not isinstance(data.get("modules"), list):
            raise StorageError("保存数据格式无效，数据库未修改。")
        seen_ids: set[str] = set()
        for key in ("cases", "modules"):
            if any(not isinstance(item, dict) or not item.get("id") for item in data[key]):
                raise StorageError(f"{key} 中存在无效记录，数据库未修改。")
            for item in data[key]:
                asset_id = str(item["id"]).strip()
                if asset_id in seen_ids:
                    raise StorageError(f"发现重复记录 ID：{asset_id}，数据库未修改。")
                seen_ids.add(asset_id)
        try:
            with self._session() as con:
                con.execute("BEGIN IMMEDIATE")
                incoming_ids = {
                    str(item["id"])
                    for key in ("cases", "modules")
                    for item in data[key]
                }
                # Keep a bounded immutable snapshot before changing or deleting an asset.
                from datetime import datetime, timezone
                import uuid
                now = datetime.now(timezone.utc).isoformat()

                def retain_revision(asset_id, kind, payload_json, reason):
                    con.execute(
                        """INSERT INTO asset_revisions
                           (revision_id,asset_id,kind,payload_json,reason,created_at)
                           VALUES(?,?,?,?,?,?)""",
                        (uuid.uuid4().hex, asset_id, kind, payload_json, reason, now),
                    )
                    con.execute(
                        """DELETE FROM asset_revisions
                           WHERE asset_id=? AND revision_id NOT IN (
                             SELECT revision_id FROM asset_revisions
                             WHERE asset_id=? ORDER BY created_at DESC,rowid DESC LIMIT 50
                           )""",
                        (asset_id, asset_id),
                    )

                has_tombstones = con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='case_tombstones'"
                ).fetchone() is not None
                for key, kind in (("cases", "case"), ("modules", "module")):
                    for item in data[key]:
                        asset_id = str(item["id"])
                        if has_tombstones and con.execute(
                            "SELECT 1 FROM case_tombstones WHERE case_id=?", (asset_id,)
                        ).fetchone():
                            raise StorageError("案例已在回收站，请先恢复后再编辑")
                        payload_json = json.dumps(item, ensure_ascii=False)
                        previous = con.execute(
                            "SELECT kind,payload_json FROM assets WHERE id=?", (asset_id,)
                        ).fetchone()
                        if previous and previous["payload_json"] != payload_json:
                            retain_revision(asset_id, previous["kind"], previous["payload_json"], "edited")
                        con.execute(
                            """INSERT INTO assets(id,kind,payload_json,created_at,updated_at)
                               VALUES(?,?,?,?,?)
                               ON CONFLICT(id) DO UPDATE SET
                                 kind=excluded.kind,
                                 payload_json=excluded.payload_json,
                                 created_at=excluded.created_at,
                                 updated_at=excluded.updated_at""",
                            (asset_id, kind, payload_json, item.get("created_at"), item.get("updated_at")),
                        )

                where = " WHERE id NOT IN (SELECT case_id FROM case_tombstones)" if has_tombstones else ""
                existing_rows = con.execute("SELECT id,kind,payload_json FROM assets" + where).fetchall()
                removed_rows = [row for row in existing_rows if row["id"] not in incoming_ids]
                for row in removed_rows:
                    retain_revision(row["id"], row["kind"], row["payload_json"], "deleted")
                    if has_tombstones and row["kind"] == "case":
                        con.execute("INSERT INTO case_tombstones(case_id) VALUES(?)", (row["id"],))
                    else:
                        con.execute("DELETE FROM assets WHERE id=?", (row["id"],))
        except Exception as exc:
            raise StorageError(f"保存失败，事务已回滚：{exc}") from exc


    def list_deleted_assets(self, limit: int = 500) -> list[dict]:
        limit = min(500, max(1, int(limit)))
        with self._session() as con:
            has_tombstones = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='case_tombstones'"
            ).fetchone() is not None
            absent = "(a.id IS NULL OR r.asset_id IN (SELECT case_id FROM case_tombstones))" if has_tombstones else "a.id IS NULL"
            rows = con.execute(
                """SELECT r.revision_id,r.asset_id,r.kind,r.created_at,r.payload_json
                   FROM asset_revisions r LEFT JOIN assets a ON a.id=r.asset_id
                   WHERE r.reason='deleted' AND """ + absent + """
                   ORDER BY r.created_at DESC,r.rowid DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            results = []
            seen_asset_ids = set()
            for row in rows:
                asset_id = row["asset_id"]
                if asset_id in seen_asset_ids:
                    continue
                seen_asset_ids.add(asset_id)
                try:
                    payload = json.loads(row["payload_json"])
                except (TypeError, json.JSONDecodeError):
                    payload = {}
                images = payload.get("images") if isinstance(payload.get("images"), list) else []
                image_name = payload.get("image") or (images[0] if images else "")
                results.append({
                    "asset_id": asset_id,
                    "revision_id": row["revision_id"],
                    "kind": row["kind"],
                    "deleted_at": row["created_at"],
                    "name": str(payload.get("name") or "未命名资产")[:160],
                    "sku": str(payload.get("sku") or payload.get("style_number") or payload.get("product_code") or "")[:100],
                    "category": str(payload.get("category") or payload.get("module_type") or "")[:80],
                    "summary": str(payload.get("body") or payload.get("description") or "")[:240],
                    "image": str(image_name or "")[:260],
                })
            return results

    def list_asset_revisions(self, asset_id: str, limit: int = 50) -> list[dict]:
        asset_id = str(asset_id or "").strip()
        if not asset_id:
            raise StorageError("资产 ID 不能为空")
        limit = min(50, max(1, int(limit)))
        with self._session() as con:
            rows = con.execute(
                """SELECT revision_id,asset_id,kind,reason,created_at,payload_json
                   FROM asset_revisions WHERE asset_id=?
                   ORDER BY created_at DESC,rowid DESC LIMIT ?""",
                (asset_id, limit),
            ).fetchall()
            results = []
            for row in rows:
                payload = json.loads(row["payload_json"])
                results.append({
                    "revision_id": row["revision_id"], "asset_id": row["asset_id"],
                    "kind": row["kind"], "reason": row["reason"],
                    "created_at": row["created_at"], "name": payload.get("name", "未命名资产"),
                    "status": payload.get("status", ""),
                })
            return results

    def get_asset_revision(self, asset_id: str, revision_id: str) -> dict:
        asset_id = str(asset_id or "").strip()
        revision_id = str(revision_id or "").strip()
        if not asset_id or not revision_id:
            raise StorageError("资产 ID 或历史版本 ID 不能为空")
        with self._session() as con:
            row = con.execute(
                """SELECT revision_id,asset_id,kind,reason,created_at,payload_json
                   FROM asset_revisions WHERE asset_id=? AND revision_id=?""",
                (asset_id, revision_id),
            ).fetchone()
            if row is None:
                raise StorageError("找不到指定的历史版本")
            return {
                "revision_id": row["revision_id"],
                "asset_id": row["asset_id"],
                "kind": row["kind"],
                "reason": row["reason"],
                "created_at": row["created_at"],
                "asset": json.loads(row["payload_json"]),
            }

    def restore_asset_revision(self, asset_id: str, revision_id: str,
                               expected_absent: bool = False) -> dict:
        asset_id, revision_id = str(asset_id or "").strip(), str(revision_id or "").strip()
        if not asset_id or not revision_id:
            raise StorageError("资产 ID 或历史版本 ID 不能为空")
        from datetime import datetime, timezone
        import uuid
        now = datetime.now(timezone.utc).isoformat()
        with self._session() as con:
            revision = con.execute(
                "SELECT * FROM asset_revisions WHERE asset_id=? AND revision_id=?",
                (asset_id, revision_id),
            ).fetchone()
            if revision is None:
                raise StorageError("找不到指定的历史版本")
            current = con.execute(
                "SELECT * FROM assets WHERE id=?", (asset_id,)
            ).fetchone()
            has_tombstones = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='case_tombstones'"
            ).fetchone() is not None
            tombstoned = has_tombstones and con.execute(
                "SELECT 1 FROM case_tombstones WHERE case_id=?", (asset_id,)
            ).fetchone() is not None
            if expected_absent and current is not None and not tombstoned:
                raise StorageError(
                    "资产已重新存在；为避免覆盖新数据，已拒绝从旧回收站快照恢复。请刷新后重试。"
                )
            if expected_absent:
                latest_deleted = con.execute(
                    """SELECT revision_id FROM asset_revisions
                       WHERE asset_id=? AND reason='deleted'
                       ORDER BY created_at DESC,rowid DESC LIMIT 1""",
                    (asset_id,),
                ).fetchone()
                if latest_deleted is None or latest_deleted["revision_id"] != revision_id:
                    raise StorageError(
                        "回收站记录已更新；当前恢复项已过期，请刷新回收站后恢复最新版本。"
                    )
            if current:
                con.execute(
                    """INSERT INTO asset_revisions
                       (revision_id,asset_id,kind,payload_json,reason,created_at)
                       VALUES(?,?,?,?,?,?)""",
                    (uuid.uuid4().hex, asset_id, current["kind"], current["payload_json"],
                     "before_restore", now),
                )
            # Repeated restores also create revisions; keep the same 50-entry
            # per-asset bound used by normal edits and generation outcomes.
            con.execute(
                """DELETE FROM asset_revisions
                   WHERE asset_id=? AND revision_id NOT IN (
                     SELECT revision_id FROM asset_revisions
                     WHERE asset_id=? ORDER BY created_at DESC,rowid DESC LIMIT 50
                   )""",
                (asset_id, asset_id),
            )
            if tombstoned:
                con.execute("DELETE FROM case_tombstones WHERE case_id=?", (asset_id,))
            payload = json.loads(revision["payload_json"])
            payload["updated_at"] = now
            con.execute(
                """INSERT INTO assets(id,kind,payload_json,created_at,updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     kind=excluded.kind,payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                (asset_id, revision["kind"], json.dumps(payload, ensure_ascii=False),
                 payload.get("created_at"), now),
            )
            return payload

    def register_scan_source(self, folder_path: str, recursive: bool = True) -> str:
        canonical = str(Path(folder_path).expanduser().resolve(strict=True))
        if not Path(canonical).is_dir():
            raise StorageError("扫描来源必须是存在的文件夹。")
        source_id = __import__("hashlib").sha256(canonical.encode("utf-8")).hexdigest()[:24]
        with self._session() as con:
            con.execute(
                """INSERT INTO scan_sources(id,folder_path,recursive,enabled)
                   VALUES(?,?,?,1)
                   ON CONFLICT(folder_path) DO UPDATE SET recursive=excluded.recursive,enabled=1""",
                (source_id, canonical, int(bool(recursive))),
            )
        return source_id

    def list_scan_sources(self) -> list[dict]:
        with self._session() as con:
            return [dict(row) for row in con.execute(
                "SELECT * FROM scan_sources ORDER BY folder_path"
            ).fetchall()]

    def get_scan_snapshot(self, source_id: str) -> dict[str, dict]:
        with self._session() as con:
            rows = con.execute(
                "SELECT * FROM ingest_files WHERE source_id=? AND status!='missing'",
                (source_id,),
            ).fetchall()
            return {row["path"]: dict(row) for row in rows}

    def save_scan_results(self, source_id: str, items: list[dict]) -> dict[str, int]:
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        added = changed = unchanged = 0
        with self._session() as con:
            source = con.execute(
                "SELECT id,folder_path FROM scan_sources WHERE id=? AND enabled=1",
                (source_id,),
            ).fetchone()
            if not source:
                raise StorageError("扫描来源不存在或已停用。")
            try:
                source_root = Path(source["folder_path"]).resolve(strict=True)
            except OSError as exc:
                raise StorageError("扫描来源文件夹已不存在，无法保存扫描结果。") from exc
            existing = {
                row["path"]: row
                for row in con.execute(
                    "SELECT path,sha256,status FROM ingest_files WHERE source_id=?",
                    (source_id,),
                ).fetchall()
            }
            seen_paths = set()
            for item in items:
                resolved_path = Path(item["path"]).resolve()
                try:
                    relative_path = resolved_path.relative_to(source_root).as_posix()
                except ValueError as exc:
                    raise StorageError("扫描结果包含来源文件夹之外的路径，已拒绝入库。") from exc
                if relative_path != str(item.get("relative_path") or "").replace(chr(92), "/"):
                    raise StorageError("扫描结果的相对路径与实际文件路径不一致，已拒绝入库。")
                path = str(resolved_path)
                seen_paths.add(path)
                old = existing.get(path)
                if old is None:
                    added += 1
                    status = "pending"
                elif old["sha256"] != item["sha256"]:
                    changed += 1
                    status = "pending"
                else:
                    unchanged += 1
                    status = old["status"] if old["status"] != "missing" else "pending"
                con.execute(
                    """INSERT INTO ingest_files(path,source_id,relative_path,name,extension,
                       media_type,size,modified_ns,sha256,status,first_seen_at,last_seen_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(path) DO UPDATE SET source_id=excluded.source_id,
                       relative_path=excluded.relative_path,name=excluded.name,
                       extension=excluded.extension,media_type=excluded.media_type,
                       size=excluded.size,modified_ns=excluded.modified_ns,sha256=excluded.sha256,
                       status=excluded.status,last_seen_at=excluded.last_seen_at""",
                    (path, source_id, item["relative_path"], item["name"], item["extension"],
                     item["media_type"], item["size"], item["modified_ns"], item["sha256"],
                     status, now, now),
                )
            # Mark only files absent from this completed scan as missing.
            for old_path in existing:
                if old_path not in seen_paths:
                    con.execute(
                        "UPDATE ingest_files SET status='missing' WHERE path=?",
                        (old_path,),
                    )
            con.execute(
                "UPDATE scan_sources SET last_scanned_at=? WHERE id=?",
                (now, source_id),
            )
        return {"added": added, "changed": changed, "unchanged": unchanged}


    def get_ingest_record(self, path: str) -> dict | None:
        canonical = str(Path(path).resolve())
        with self._session() as con:
            row = con.execute(
                "SELECT * FROM ingest_files WHERE path=? AND status IN ('pending','approved')",
                (canonical,),
            ).fetchone()
            return dict(row) if row else None

    def list_pending_ingest(self, source_id: str | None = None) -> list[dict]:
        query = "SELECT * FROM ingest_files WHERE status='pending'"
        args: tuple = ()
        if source_id:
            query += " AND source_id=?"
            args = (source_id,)
        query += " ORDER BY last_seen_at DESC"
        with self._session() as con:
            return [dict(row) for row in con.execute(query, args).fetchall()]

    def set_ingest_status(self, path: str, status: str) -> None:
        if status not in {"approved", "ignored", "pending"}:
            raise StorageError("不支持的采集审核状态。")
        with self._session() as con:
            cur = con.execute("UPDATE ingest_files SET status=? WHERE path=?", (status, str(Path(path).resolve())))
            if cur.rowcount != 1:
                raise StorageError("找不到对应的采集文件记录。")


    def save_ai_analysis(self, prompt_path: str, asset_path: str,
                         prompt_sha256: str, asset_sha256: str,
                         model: str, result: dict) -> str:
        import uuid
        analysis_id = uuid.uuid4().hex
        with self._session() as con:
            con.execute(
                """INSERT INTO ai_analyses
                   (id,prompt_path,asset_path,prompt_sha256,asset_sha256,model,result_json,status)
                   VALUES(?,?,?,?,?,?,?,'review')""",
                (analysis_id, prompt_path, asset_path, prompt_sha256, asset_sha256,
                 model, json.dumps(result, ensure_ascii=False)),
            )
        return analysis_id

    def list_ai_analyses(self, status: str | None = None) -> list[dict]:
        query = "SELECT * FROM ai_analyses"
        args: tuple = ()
        if status:
            if status not in {"review", "approved", "rejected"}:
                raise StorageError("AI 分析状态无效")
            query += " WHERE status=?"
            args = (status,)
        query += " ORDER BY created_at DESC"
        with self._session() as con:
            rows = con.execute(query, args).fetchall()
            return [{**dict(row), "result": json.loads(row["result_json"])} for row in rows]

    def _verified_ingest_source_path(self, con, raw_path: str, expected_media_type: str,
                                     expected_sha256: str) -> Path:
        raw_path = str(raw_path or "")
        if not raw_path:
            raise StorageError("AI 分析缺少原始素材路径，请重新扫描并分析")
        candidate = Path(raw_path)
        if any(_is_link_or_junction(component) for component in (candidate, *candidate.parents)):
            raise StorageError("AI 分析来源文件已变为符号链接或目录联接，拒绝入库")
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise StorageError("AI 分析来源文件已不存在，请重新扫描并分析") from exc
        try:
            same_file = os.path.samefile(raw_path, resolved)
        except OSError as exc:
            raise StorageError("AI 分析来源文件已不存在，请重新扫描并分析") from exc
        if not same_file:
            raise StorageError("AI 分析来源文件路径已发生变化，请重新扫描并分析")

        record = con.execute(
            """SELECT f.media_type,f.sha256,s.folder_path
               FROM ingest_files f
               JOIN scan_sources s ON s.id=f.source_id
               WHERE f.path=?""",
            (str(resolved),),
        ).fetchone()
        if record is None:
            raise StorageError("AI 分析来源已不在已扫描文件夹中，请重新扫描并分析")
        if record["media_type"] != expected_media_type:
            raise StorageError("AI 分析来源文件类型与扫描记录不一致，请重新扫描并分析")
        if str(record["sha256"]).lower() != str(expected_sha256).lower():
            raise StorageError("AI 分析来源文件指纹与扫描记录不一致，请重新扫描并分析")

        source_root_path = Path(record["folder_path"])
        if _is_link_or_junction(source_root_path):
            raise StorageError("AI 分析来源文件夹已变为符号链接或目录联接，拒绝入库")
        try:
            source_root = source_root_path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise StorageError("AI 分析来源文件夹已不存在，请重新扫描并分析") from exc
        if os.path.normcase(os.path.normpath(str(source_root))) != os.path.normcase(
            os.path.normpath(str(record["folder_path"]))
        ):
            raise StorageError("AI 分析来源文件夹路径已发生变化，拒绝入库")
        try:
            resolved.relative_to(source_root)
        except ValueError as exc:
            raise StorageError("AI 分析来源文件已移出原扫描文件夹，拒绝入库") from exc
        if not resolved.is_file():
            raise StorageError("AI 分析来源文件不再是普通文件，拒绝入库")

        digest = hashlib.sha256()
        with resolved.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != str(expected_sha256).lower():
            raise StorageError("AI 分析来源文件在分析后发生变化，请重新扫描并分析")
        return resolved

    def approve_ai_analysis(self, analysis_id: str) -> str:
        copied_paths: list[Path] = []
        try:
            return self._approve_ai_analysis(analysis_id, copied_paths)
        except BaseException as exc:
            cleanup_errors = []
            for copied_path in copied_paths:
                try:
                    copied_path.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    cleanup_errors.append(
                        f"{copied_path.name}: {type(cleanup_error).__name__}: {cleanup_error}"
                    )
            if cleanup_errors:
                raise StorageError(
                    "AI 案例入库失败，且部分参考图片清理不完整："
                    + "; ".join(cleanup_errors)[:1200]
                ) from exc
            raise

    def _approve_ai_analysis(self, analysis_id: str, copied_paths: list[Path]) -> str:
        import uuid
        with self._session() as con:
            row = con.execute(
                "SELECT * FROM ai_analyses WHERE id=? AND status='review'",
                (analysis_id,),
            ).fetchone()
            if row is None:
                raise StorageError("找不到待确认的 AI 分析结果")
            result = json.loads(row["result_json"])
            asset_id = uuid.uuid4().hex
            now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
            title = str(result.get("title") or Path(row["prompt_path"]).stem).strip()[:120]
            prompt_file = self._verified_ingest_source_path(
                con, row["prompt_path"], "text", row["prompt_sha256"]
            )
            prompt_text = result.get("prompt_text")
            if not isinstance(prompt_text, str) or not prompt_text.strip():
                raise StorageError("分析结果缺少原始提示词，不能入库")
            try:
                source_prompt_text = prompt_file.read_bytes().decode("utf-8-sig", errors="replace")
            except OSError as exc:
                raise StorageError("原始提示词文件读取失败，请重新扫描并分析") from exc
            if prompt_text != source_prompt_text:
                raise StorageError("原始提示词与来源文件内容不一致，拒绝入库")

            asset_paths = result.get("asset_paths") or [row["asset_path"]]
            asset_hashes = result.get("asset_sha256s") or [row["asset_sha256"]]
            if (not isinstance(asset_paths, list) or not 1 <= len(asset_paths) <= 8
                    or not isinstance(asset_hashes, list) or len(asset_hashes) != len(asset_paths)):
                raise StorageError("关联素材记录不完整，不能入库")
            if len(set(map(str, asset_paths))) != len(asset_paths):
                raise StorageError("关联素材重复，不能入库")

            references = self.home / "references"
            image_names = []
            verified_assets = []
            for raw_path, expected_hash in zip(asset_paths, asset_hashes):
                expected_hash = str(expected_hash).lower()
                media_type = (
                    "video" if Path(str(raw_path)).suffix.lower()
                    in {".mp4", ".mov", ".m4v", ".webm", ".avi"}
                    else "image"
                )
                asset_file = self._verified_ingest_source_path(
                    con, str(raw_path), media_type, expected_hash
                )
                copied_name = ""
                if media_type == "image":
                    if _is_link_or_junction(references):
                        raise StorageError("参考图片目录是符号链接或 Windows 目录联接，拒绝写入。")
                    references.mkdir(parents=True, exist_ok=True)
                    if _is_link_or_junction(references):
                        raise StorageError("参考图片目录在写入前发生变化，拒绝写入。")
                    try:
                        home_root = self.home.resolve(strict=True)
                        reference_root = references.resolve(strict=True)
                        if reference_root.parent != home_root:
                            raise ValueError("参考图片目录已超出本地数据目录")
                    except (OSError, RuntimeError, ValueError) as exc:
                        raise StorageError("参考图片目录路径不安全，拒绝写入。") from exc
                    copied_name = "history_" + uuid.uuid4().hex[:16] + asset_file.suffix.lower()
                    destination = reference_root / copied_name
                    copy_digest = hashlib.sha256()
                    with asset_file.open("rb") as source:
                        with destination.open("xb") as target:
                            copied_paths.append(destination)
                            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                                copy_digest.update(chunk)
                                target.write(chunk)
                    if copy_digest.hexdigest() != expected_hash:
                        raise StorageError(
                            f"关联素材在复制过程中发生变化，请重新扫描并分析：{asset_file.name}"
                        )
                    shutil.copystat(asset_file, destination)
                    image_names.append(copied_name)
                verified_assets.append((str(asset_file), media_type, str(expected_hash), copied_name))

            raw_tags = result.get("tags", [])
            if isinstance(raw_tags, str):
                raw_tags = raw_tags.replace("，", ",").split(",")
            if not isinstance(raw_tags, list):
                raw_tags = []
            tags = [
                str(tag).strip()[:80]
                for tag in raw_tags
                if isinstance(tag, (str, int, float)) and str(tag).strip()
            ][:50]
            item = {
                "id": asset_id, "name": title, "sku": str(result.get("sku") or "").strip(),
                "category": str(result.get("category") or "其他"),
                "subcategory": str(result.get("subcategory") or ""),
                "material": str(result.get("material") or ""), "color": str(result.get("color") or ""),
                "silhouette": str(result.get("silhouette") or ""), "fit": str(result.get("fit") or ""),
                "details": result.get("details") if isinstance(result.get("details"), list) else [],
                "scene": str(result.get("scene") or ""),
                "constraints": result.get("constraints") if isinstance(result.get("constraints"), list) else [],
                "model": str(result.get("model") or ""),
                "status": "待验证", "tags": tags,
                "body": prompt_text,
                "notes": "AI 历史案例采集；已人工确认。摘要：" + str(result.get("prompt_summary") or ""),
                "image": image_names[0] if image_names else "", "images": image_names,
                "image_roles": [
                    {
                        "asset_index": int(role.get("asset_index", 0)),
                        "role": str(role.get("role") or "unknown")[:20],
                        "role_label": str(role.get("role_label") or "待人工确认")[:40],
                        "priority": str(role.get("priority") or "supporting")[:20],
                        "governs": [str(value)[:40] for value in role.get("governs", [])[:6]]
                            if isinstance(role.get("governs"), list) else [],
                        "observation": str(role.get("observation") or "")[:500],
                        "source_path": str(role.get("asset_path") or "")[:1000],
                    }
                    for role in (result.get("asset_roles") or [])
                    if isinstance(role, dict)
                ][:8],
                "outfit_items": [
                    {
                        "item_index": int(part.get("item_index", index)),
                        "category": str(part.get("category") or "其他")[:20],
                        "subcategory": str(part.get("subcategory") or "")[:60],
                        "name": str(part.get("name") or "")[:120],
                        "color": str(part.get("color") or "")[:60],
                        "material": str(part.get("material") or "")[:120],
                        "silhouette": str(part.get("silhouette") or "")[:80],
                        "fit": str(part.get("fit") or "")[:60],
                        "details": [str(value)[:100] for value in part.get("details", [])[:20]]
                            if isinstance(part.get("details"), list) else [],
                        "evidence": str(part.get("evidence") or "")[:300],
                        "confidence": max(0.0, min(1.0, float(part.get("confidence", 0) or 0))),
                    }
                    for index, part in enumerate(result.get("outfit_items") or [], start=1)
                    if isinstance(part, dict)
                ][:12],
                "outfit_relations": [
                    {
                        "source_item": int(rel.get("source_item", 0)),
                        "target_item": int(rel.get("target_item", 0)),
                        "relation": str(rel.get("relation") or "unknown")[:20],
                        "evidence": str(rel.get("evidence") or "")[:240],
                    }
                    for rel in (result.get("outfit_relations") or [])
                    if isinstance(rel, dict)
                ][:20],
                "provenance": {
                    "type": "ai_history_import",
                    "analysis_id": analysis_id,
                    "model": str(row["model"] or ""),
                    "imported_at": now,
                    "source_prompt": {
                        "path": str(prompt_file),
                        "sha256": str(row["prompt_sha256"]),
                    },
                    "source_assets": [
                        {
                            "path": original_path,
                            "media_type": media_type,
                            "sha256": digest,
                        }
                        for original_path, media_type, digest, _ in verified_assets
                    ],
                },
                "created_at": now, "updated_at": now,
            }
            con.execute(
                "INSERT INTO assets(id,kind,payload_json,created_at,updated_at) VALUES(?,?,?,?,?)",
                (asset_id, "case", json.dumps(item, ensure_ascii=False), now, now),
            )
            for path, media_type, digest, _ in verified_assets:
                con.execute(
                    """INSERT OR IGNORE INTO attachments
                       (asset_id,relative_path,media_type,role,sha256,created_at)
                       VALUES(?,?,?,?,?,?)""",
                    (asset_id, path, media_type, "history_reference", digest, now),
                )
            con.execute(
                """INSERT OR IGNORE INTO attachments
                   (asset_id,relative_path,media_type,role,sha256,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (asset_id, str(prompt_file), "text", "source_prompt", row["prompt_sha256"], now),
            )
            con.execute(
                "UPDATE ai_analyses SET status='approved',approved_asset_id=? WHERE id=?",
                (asset_id, analysis_id),
            )
        return asset_id

    def reject_ai_analysis(self, analysis_id: str) -> None:
        with self._session() as con:
            cur = con.execute(
                "UPDATE ai_analyses SET status='rejected' WHERE id=? AND status='review'",
                (analysis_id,),
            )
            if cur.rowcount != 1:
                raise StorageError("找不到待拒绝的 AI 分析结果")

    def record_generation_outcome(self, case_id: str, outcome: str,
                                  notes: str = "") -> dict:
        case_id = str(case_id or "").strip()
        if not case_id:
            raise StorageError("案例 ID 不能为空")
        if outcome not in {"success", "failed"}:
            raise StorageError("生成结果只支持成功或失败")
        from datetime import datetime, timezone
        import uuid

        now = datetime.now(timezone.utc).isoformat()
        note = str(notes or "").strip()[:1000]
        status = "已验证成功" if outcome == "success" else "失败案例"
        with self._session() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT kind,payload_json FROM assets WHERE id=?", (case_id,)
            ).fetchone()
            if row is None or row["kind"] != "case":
                raise StorageError("找不到对应的案例资产")
            previous_payload = row["payload_json"]
            item = json.loads(previous_payload)
            item["status"] = status
            item["last_generation_outcome"] = outcome
            item["last_generation_note"] = note
            item["last_generation_at"] = now
            con.execute(
                """INSERT INTO asset_revisions
                   (revision_id,asset_id,kind,payload_json,reason,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (uuid.uuid4().hex, case_id, "case", previous_payload,
                 "generation_outcome", now),
            )
            con.execute(
                """DELETE FROM asset_revisions
                   WHERE asset_id=? AND revision_id NOT IN (
                     SELECT revision_id FROM asset_revisions
                     WHERE asset_id=? ORDER BY created_at DESC,rowid DESC LIMIT 50
                   )""",
                (case_id, case_id),
            )
            con.execute(
                "UPDATE assets SET payload_json=?,updated_at=? WHERE id=? AND kind='case'",
                (json.dumps(item, ensure_ascii=False), now, case_id),
            )
            cur = con.execute(
                """INSERT INTO generation_feedback(case_id,outcome,notes,created_at)
                   VALUES(?,?,?,?)""",
                (case_id, outcome, note, now),
            )
            feedback_id = int(cur.lastrowid)
            con.execute(
                """DELETE FROM generation_feedback WHERE id NOT IN
                   (SELECT id FROM generation_feedback ORDER BY id DESC LIMIT 5000)"""
            )
        return {
            "feedback_id": feedback_id,
            "case_id": case_id,
            "outcome": outcome,
            "status": status,
            "created_at": now,
        }

    def record_retrieval_feedback(self, query: str, case_id: str,
                                   score: float, feedback: str) -> int:
        query = str(query or "").strip()[:500]
        case_id = str(case_id or "").strip()[:128]
        if not query or not case_id:
            raise StorageError("检索词和案例 ID 不能为空")
        if feedback not in {"useful", "not_relevant"}:
            raise StorageError("不支持的检索反馈类型")
        try:
            score = max(0.0, min(1.0, float(score)))
        except (TypeError, ValueError):
            raise StorageError("匹配分数无效")
        from datetime import datetime, timezone
        with self._session() as con:
            exists = con.execute(
                "SELECT 1 FROM assets WHERE id=? AND kind='case'", (case_id,)
            ).fetchone()
            if exists is None:
                raise StorageError("找不到对应的案例资产")
            cur = con.execute(
                """INSERT INTO retrieval_feedback
                   (query_text,case_id,score,feedback,created_at)
                   VALUES(?,?,?,?,?)""",
                (query, case_id, score, feedback, datetime.now(timezone.utc).isoformat()),
            )
            con.execute(
                """DELETE FROM retrieval_feedback WHERE id NOT IN
                   (SELECT id FROM retrieval_feedback ORDER BY id DESC LIMIT 5000)"""
            )
            return int(cur.lastrowid)

    def get_retrieval_analytics(self) -> dict:
        with self._session() as con:
            total = con.execute("SELECT COUNT(*) FROM retrieval_feedback").fetchone()[0]
            counts = {
                row["feedback"]: row["count"]
                for row in con.execute(
                    "SELECT feedback,COUNT(*) AS count FROM retrieval_feedback GROUP BY feedback"
                ).fetchall()
            }
            top = con.execute(
                """SELECT case_id,COUNT(*) AS feedback_count,
                          SUM(CASE WHEN feedback='useful' THEN 1 ELSE 0 END) AS useful_count,
                          AVG(score) AS average_score
                   FROM retrieval_feedback GROUP BY case_id
                   ORDER BY useful_count DESC,feedback_count DESC LIMIT 10"""
            ).fetchall()
            generation = con.execute(
                "SELECT outcome,COUNT(*) AS count FROM generation_feedback GROUP BY outcome"
            ).fetchall()
            generation_counts = {row["outcome"]: int(row["count"]) for row in generation}
            return {
                "total_feedback": int(total),
                "useful": int(counts.get("useful", 0)),
                "not_relevant": int(counts.get("not_relevant", 0)),
                "generation_success": generation_counts.get("success", 0),
                "generation_failed": generation_counts.get("failed", 0),
                "generation_feedback_total": sum(generation_counts.values()),
                "top_cases": [dict(row) for row in top],
            }

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        key = str(key or "").strip()
        if not key:
            raise StorageError("设置键不能为空")
        with self._session() as con:
            row = con.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        key = str(key or "").strip()
        if not key:
            raise StorageError("设置键不能为空")
        with self._session() as con:
            con.execute(
                "INSERT INTO metadata(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )

    def backup(self, destination: Path | None = None) -> Path:
        with self.operation():
            with self._db_lock:
                return self._backup_locked(destination)

    def _backup_locked(self, destination: Path | None = None) -> Path:
        target = Path(destination) if destination else self.home / "backups" / "prompt_library.sqlite3"
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="db_backup_", suffix=".sqlite3", dir=target.parent)
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            source = self._connect()
            dest = sqlite3.connect(temp_path)
            try:
                source.backup(dest)
            finally:
                dest.close()
                source.close()
            # Validate backup before atomically publishing it.
            check = sqlite3.connect(temp_path)
            try:
                result = check.execute("PRAGMA quick_check").fetchone()
                if not result or result[0] != "ok":
                    raise StorageError("备份完整性检查失败。")
            finally:
                check.close()
            os.replace(temp_path, target)
            return target
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def export_json(self, destination: Path) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = self.load_data()
        temp = destination.with_suffix(destination.suffix + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, destination)
        return destination

    def create_full_backup(self, references_dir: Path | None = None,
                           app_version: str = "unknown") -> Path:
        with self.operation():
            with self._db_lock:
                return self._create_full_backup_locked(references_dir, app_version)

    def _create_full_backup_locked(self, references_dir: Path | None = None,
                                   app_version: str = "unknown") -> Path:
        """Create a verified ZIP backup of the SQLite database, JSON data and local references.

        The live database is copied through SQLite's backup API so WAL contents are
        included consistently. Source reference files are read-only and never moved.
        """
        from datetime import datetime, timezone

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_dir = self.home / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / f"PromptAtelier_full_backup_{stamp}.zip"

        with tempfile.TemporaryDirectory(prefix=".backup_build_", dir=str(self.home)) as work:
            work_dir = Path(work)
            db_copy = self.backup(work_dir / "prompt_library.sqlite3")
            # Export JSON from the exact SQLite snapshot above. Reading the live
            # database a second time could race with a concurrent save and create
            # a ZIP whose own database and JSON disagree.
            json_copy = work_dir / "prompt_library.json"
            try:
                snapshot = sqlite3.connect(str(db_copy))
                has_tombstones = snapshot.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='case_tombstones'"
                ).fetchone() is not None
                where = " WHERE id NOT IN (SELECT case_id FROM case_tombstones)" if has_tombstones else ""
                rows = snapshot.execute(
                    "SELECT kind,payload_json FROM assets" + where + " ORDER BY updated_at DESC, rowid DESC"
                ).fetchall()
                version_row = snapshot.execute(
                    "SELECT value FROM metadata WHERE key='schema_version'"
                ).fetchone()
                snapshot_version = str(version_row[0]) if version_row else ""
                snapshot.close()
                if snapshot_version not in {"1", "2"}:
                    raise StorageError("备份数据库版本不受支持")
                snapshot_data = {"version": 3, "cases": [], "modules": []}
                for kind, payload_json in rows:
                    item = json.loads(payload_json)
                    snapshot_data["cases" if kind == "case" else "modules"].append(item)
                json_copy.write_text(
                    json.dumps(snapshot_data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as exc:
                raise StorageError("无法从一致的数据库快照生成 JSON 备份") from exc
            payloads: dict[str, Path] = {
                "database/prompt_library.sqlite3": db_copy,
                "data/prompt_library.json": json_copy,
            }

            if references_dir:
                source = Path(references_dir)
                if _is_link_or_junction(source):
                    raise StorageError("参考图片目录是符号链接或 Windows 目录联接，拒绝创建不完整备份。")
                if source.is_dir():
                    source_root = source.resolve(strict=True)
                    for item in sorted(source_root.rglob("*")):
                        try:
                            if _is_link_or_junction(item) or not item.is_file():
                                continue
                            resolved_item = item.resolve(strict=True)
                            relative = resolved_item.relative_to(source_root).as_posix()
                            payloads["references/" + relative] = resolved_item
                        except (OSError, ValueError):
                            # Skip links/reparse points that escape the reference root
                            # and files that disappear during a backup.
                            continue

            def file_sha256(path: Path) -> str:
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                return digest.hexdigest()

            checksums = {name: file_sha256(path) for name, path in payloads.items()}
            manifest = {
                "format": "PromptAtelierFullBackup",
                "format_version": 2 if snapshot_version == "2" else 1,
                "schema_version": int(snapshot_version),
                "app_version": str(app_version),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "files": checksums,
            }

            temp_archive = target.with_suffix(".zip.tmp")
            try:
                with zipfile.ZipFile(temp_archive, "w", compression=zipfile.ZIP_DEFLATED,
                                     compresslevel=6) as archive:
                    for name, path in payloads.items():
                        archive.write(path, arcname=name)
                    archive.writestr(
                        "backup_manifest.json",
                        json.dumps(manifest, ensure_ascii=False, indent=2),
                    )
                # Re-open and verify every archived payload before publishing it.
                with zipfile.ZipFile(temp_archive, "r") as archive:
                    bad_member = archive.testzip()
                    if bad_member:
                        raise StorageError(f"备份压缩包完整性检查失败：{bad_member}")
                    for name, expected in checksums.items():
                        digest = hashlib.sha256()
                        with archive.open(name, "r") as stream:
                            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                                digest.update(chunk)
                        if digest.hexdigest() != expected:
                            raise StorageError(f"备份文件校验失败：{name}")
                os.replace(temp_archive, target)
            finally:
                if temp_archive.exists():
                    temp_archive.unlink()
        return target

    def restore_full_backup(self, archive_path: Path) -> dict:
        with self.exclusive_operation():
            with self._db_lock:
                return self._restore_full_backup_locked(archive_path)

    def _restore_full_backup_locked(self, archive_path: Path) -> dict:
        """Verify a full backup completely, then atomically replace local data and references."""
        archive_path = Path(archive_path).resolve()
        if not archive_path.is_file():
            raise StorageError("完整备份文件不存在")
        if archive_path.stat().st_size > 500 * 1024 * 1024:
            raise StorageError("备份压缩包超过 500MB，拒绝恢复")

        stage_dir = Path(tempfile.mkdtemp(prefix=".restore_stage_", dir=str(self.home)))
        rollback_dir = Path(tempfile.mkdtemp(prefix=".restore_rollback_", dir=str(self.home)))
        swapped_db = swapped_json = swapped_refs = False
        keep_rollback_dir = False
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                infos = archive.infolist()
                if len(infos) > 20000:
                    raise StorageError("备份文件数量超过安全上限")
                names = [info.filename for info in infos]
                if len(names) != len(set(names)):
                    raise StorageError("备份中存在重复文件路径")
                if "backup_manifest.json" not in names:
                    raise StorageError("缺少备份清单，无法确认备份来源")
                manifest_info = archive.getinfo("backup_manifest.json")
                if manifest_info.file_size > 5 * 1024 * 1024:
                    raise StorageError("备份清单异常过大")
                manifest = json.loads(archive.read("backup_manifest.json").decode("utf-8"))
                if (manifest.get("format") != "PromptAtelierFullBackup"
                        or manifest.get("format_version") not in (1, 2)
                        or not isinstance(manifest.get("files"), dict)):
                    raise StorageError("备份格式或版本不受支持")
                expected_files = manifest["files"]
                actual_files = [name for name in names if name != "backup_manifest.json"]
                if set(actual_files) != set(expected_files):
                    raise StorageError("备份清单与压缩包文件列表不一致")
                required = {
                    "database/prompt_library.sqlite3",
                    "data/prompt_library.json",
                }
                if not required.issubset(expected_files):
                    raise StorageError("备份缺少数据库或 JSON 数据文件")

                total_size = 0
                normalized_paths = set()
                for info in infos:
                    if info.filename == "backup_manifest.json":
                        continue
                    name = info.filename
                    relative = PurePosixPath(name)
                    raw_parts = name.split("/")
                    windows_reserved = {"CON", "PRN", "AUX", "NUL"} | {
                        f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
                    }
                    unsafe_component = any(
                        not part or part in {".", ".."} or part.endswith((".", " "))
                        or any(char in part for char in '<>:"|?*')
                        or part.split(".", 1)[0].upper() in windows_reserved
                        for part in raw_parts
                    )
                    normalized_key = name.casefold()
                    unix_mode = (info.external_attr >> 16) & 0xFFFF
                    if (not name or "\\" in name or "\x00" in name
                            or relative.is_absolute() or unsafe_component
                            or info.is_dir() or stat.S_ISLNK(unix_mode)
                            or normalized_key in normalized_paths
                            or not (name in required or name.startswith("references/"))):
                        raise StorageError(f"备份包含不安全的文件路径：{name[:120]}")
                    normalized_paths.add(normalized_key)
                    if info.file_size > 1024 * 1024 * 1024:
                        raise StorageError(f"备份单个文件超过 1GB：{name[:120]}")
                    total_size += info.file_size
                    if total_size > 2 * 1024 * 1024 * 1024:
                        raise StorageError("备份解压总量超过 2GB，拒绝恢复")
                    expected_hash = str(expected_files.get(name) or "").lower()
                    if len(expected_hash) != 64 or any(ch not in "0123456789abcdef" for ch in expected_hash):
                        raise StorageError(f"备份清单中的 SHA-256 格式无效：{name[:120]}")

                    target = stage_dir.joinpath(*relative.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    with archive.open(info, "r") as source, target.open("wb") as destination:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            digest.update(chunk)
                            destination.write(chunk)
                    if digest.hexdigest() != expected_hash:
                        raise StorageError(f"备份文件 SHA-256 校验失败：{name}")

            staged_db = stage_dir / "database" / "prompt_library.sqlite3"
            staged_json = stage_dir / "data" / "prompt_library.json"
            try:
                check = sqlite3.connect(str(staged_db))
                integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
                tables = {row[0] for row in check.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()}
                foreign_key_errors = check.execute("PRAGMA foreign_key_check").fetchall()
                check.close()
            except sqlite3.DatabaseError as exc:
                raise StorageError("备份数据库无法打开或已损坏") from exc
            if integrity != "ok" or foreign_key_errors or not {"assets", "metadata"}.issubset(tables):
                raise StorageError("备份数据库完整性、外键或结构检查未通过")
            if manifest.get("format_version") == 2:
                inspection = sqlite3.connect(str(staged_db))
                try:
                    records = inspection.execute(
                        "SELECT id,relative_path,media_type,sha256 FROM attachments"
                    ).fetchall()
                    names = {row[0] for row in inspection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    pending = ({row[0] for row in inspection.execute(
                        "SELECT attachment_id FROM unresolved_video_refs WHERE status='pending'")}
                        if "unresolved_video_refs" in names else set())
                finally:
                    inspection.close()
                for attachment_id, relative_path, media_type, digest in records:
                    status = _attachment_status(stage_dir / "references", relative_path, media_type, digest)
                    if attachment_id in pending and _is_legacy_absolute_video({
                            "relative_path": relative_path, "media_type": media_type, "status": status}):
                        continue
                    if status != "verified":
                        raise StorageError("备份素材未通过验证：" + str(status))

            try:
                restored_json = json.loads(staged_json.read_text(encoding="utf-8"))
            except Exception as exc:
                raise StorageError("备份 JSON 数据无法解析") from exc
            if (not isinstance(restored_json, dict)
                    or not isinstance(restored_json.get("cases"), list)
                    or not isinstance(restored_json.get("modules"), list)):
                raise StorageError("备份 JSON 数据结构不正确")
            seen_ids = set()
            for key in ("cases", "modules"):
                for item in restored_json[key]:
                    if not isinstance(item, dict) or not str(item.get("id") or "").strip():
                        raise StorageError(f"备份 {key} 中存在无效资产记录")
                    asset_id = str(item["id"]).strip()
                    if asset_id in seen_ids:
                        raise StorageError(f"备份中存在重复资产 ID：{asset_id}")
                    seen_ids.add(asset_id)

            # The ZIP checksum only proves internal consistency, not that the
            # JSON and SQLite payloads describe the same library. Verify both.
            try:
                verify_db = sqlite3.connect(str(staged_db))
                has_tombstones = verify_db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='case_tombstones'"
                ).fetchone() is not None
                where = " WHERE id NOT IN (SELECT case_id FROM case_tombstones)" if has_tombstones else ""
                rows = verify_db.execute(
                    "SELECT kind,payload_json FROM assets" + where + " ORDER BY updated_at DESC, rowid DESC"
                ).fetchall()
                verify_db.close()
                database_payload = {"version": 3, "cases": [], "modules": []}
                for kind, payload_json in rows:
                    item = json.loads(payload_json)
                    database_payload["cases" if kind == "case" else "modules"].append(item)
            except Exception as exc:
                raise StorageError("备份数据库资产内容无法完整读取") from exc
            if (restored_json.get("cases") != database_payload["cases"]
                    or restored_json.get("modules") != database_payload["modules"]):
                raise StorageError("备份数据库与 JSON 内容不一致，拒绝恢复")

            # Never replace a V2 store with a V1 backup. The old application
            # cannot apply this check, so file-level downgrade protection remains
            # a separate release gate.
            with self._session() as live:
                row = live.execute(
                    "SELECT value FROM metadata WHERE key='schema_version'"
                ).fetchone()
                live_version = str(row[0]) if row else ""
            restored = sqlite3.connect(str(staged_db))
            try:
                row = restored.execute(
                    "SELECT value FROM metadata WHERE key='schema_version'"
                ).fetchone()
                restored_version = str(row[0]) if row else ""
            finally:
                restored.close()
            if restored_version not in {"1", "2"}:
                raise StorageError("备份数据库版本不受支持")
            if manifest.get("format_version") == 2:
                if manifest.get("schema_version") != int(restored_version):
                    raise StorageError("备份清单与数据库版本不一致")
            elif restored_version != "1":
                raise StorageError("V2 数据库必须使用版本 2 备份格式")
            if live_version == "2" and restored_version != "2":
                raise StorageError("不能使用旧版本备份覆盖 V2 数据库；请先在隔离目录恢复并迁移")

            safety_backup = self.create_full_backup(
                self.home / "references", app_version="pre-restore"
            )
            old_db = rollback_dir / "old.sqlite3"
            self.backup(old_db)
            old_json = rollback_dir / "old_prompt_library.json"
            if self.legacy_json.is_file():
                shutil.copy2(self.legacy_json, old_json)
            old_refs = rollback_dir / "old_references"
            references_dir = self.home / "references"
            staged_refs = stage_dir / "references"
            if references_dir.is_dir():
                os.replace(references_dir, old_refs)
            swapped_refs = True
            try:
                if references_dir.exists():
                    shutil.rmtree(references_dir)
                if staged_refs.is_dir():
                    os.replace(staged_refs, references_dir)
                else:
                    references_dir.mkdir(parents=True, exist_ok=True)

                for suffix in ("-wal", "-shm"):
                    sidecar = Path(str(self.db_path) + suffix)
                    if sidecar.exists():
                        sidecar.unlink()
                os.replace(staged_db, self.db_path)
                swapped_db = True
                os.replace(staged_json, self.legacy_json)
                swapped_json = True

                # Re-apply additive schema migrations so older backups gain new tables/indexes.
                self._initialize()
                verified = self.load_data()
                if len(verified.get("cases", [])) != len(restored_json["cases"]):
                    raise StorageError("恢复后案例数量与备份清单不一致")
                if len(verified.get("modules", [])) != len(restored_json["modules"]):
                    raise StorageError("恢复后模块数量与备份清单不一致")
            except Exception as exc:
                rollback_errors = []
                if swapped_db:
                    try:
                        for suffix in ("-wal", "-shm"):
                            sidecar = Path(str(self.db_path) + suffix)
                            if sidecar.exists():
                                sidecar.unlink()
                        if self.db_path.exists():
                            self.db_path.unlink()
                        os.replace(old_db, self.db_path)
                    except Exception as rollback_error:
                        rollback_errors.append(f"SQLite 数据库：{type(rollback_error).__name__}: {rollback_error}")
                if swapped_json:
                    try:
                        if self.legacy_json.exists():
                            self.legacy_json.unlink()
                        if old_json.exists():
                            os.replace(old_json, self.legacy_json)
                    except Exception as rollback_error:
                        rollback_errors.append(f"JSON 数据：{type(rollback_error).__name__}: {rollback_error}")
                if swapped_refs:
                    try:
                        if references_dir.exists():
                            shutil.rmtree(references_dir)
                        if old_refs.exists():
                            os.replace(old_refs, references_dir)
                    except Exception as rollback_error:
                        rollback_errors.append(f"参考图片目录：{type(rollback_error).__name__}: {rollback_error}")
                if rollback_errors:
                    keep_rollback_dir = True
                    details = "; ".join(rollback_errors)[:2000]
                    safety_path = self.home / "backups" / safety_backup.name
                    raise StorageError(
                        "恢复失败且自动回滚未完整完成；原始数据恢复副本保留在 "
                        f"{rollback_dir}，恢复前完整备份位于 {safety_path}。"
                        f"请勿删除这两个备份位置。回滚失败项：{details}"
                    ) from exc
                if isinstance(exc, StorageError):
                    raise
                raise StorageError("恢复过程中发生错误，已回滚原数据") from exc

            return {
                "ok": True,
                "cases": len(restored_json["cases"]),
                "modules": len(restored_json["modules"]),
                "references": sum(1 for path in references_dir.rglob("*") if path.is_file()),
                "backup_created_at": manifest.get("created_at", ""),
                "safety_backup": safety_backup.name,
            }
        finally:
            shutil.rmtree(stage_dir, ignore_errors=True)
            if not keep_rollback_dir:
                shutil.rmtree(rollback_dir, ignore_errors=True)


def _id():
    return uuid.uuid4().hex


class KnowledgeStore:
    def __init__(self, store):
        self.store = store
        with store._session() as con:
            version = con.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone()
            if version is None or str(version[0]) != "2":
                raise StorageError("请先完成安全迁移，再使用服装知识库")

    def import_legacy_json(self, payload):
        if not isinstance(payload, dict):
            raise StorageError("导入文件必须是对象")
        if not isinstance(payload.get("cases"), list) or not isinstance(payload.get("modules"), list):
            raise StorageError("导入文件缺少案例或模块数组")
        report = {"inserted": 0, "existing": 0, "errors": [], "warnings": []}
        seen = set()
        with self.store._session() as con:
            for collection, kind in (("cases", "case"), ("modules", "module")):
                for index, item in enumerate(payload[collection]):
                    location = f"{collection}[{index}]"
                    if not isinstance(item, dict) or not str(item.get("id") or "").strip():
                        report["errors"].append({"record": location, "reason": "记录或 ID 无效"})
                        continue
                    asset_id = str(item["id"]).strip()
                    if not isinstance(item["id"], str) or asset_id != item["id"]:
                        report["errors"].append({"record": location, "reason": "ID 必须是无首尾空格的字符串"})
                        continue
                    if asset_id in seen:
                        report["errors"].append({"record": location, "reason": "导入文件内 ID 重复"})
                        continue
                    seen.add(asset_id)
                    if con.execute("SELECT 1 FROM assets WHERE id=?", (asset_id,)).fetchone():
                        report["existing"] += 1
                        continue
                    con.execute(
                        "INSERT INTO assets(id,kind,payload_json,created_at,updated_at) VALUES(?,?,?,?,?)",
                        (asset_id, kind, json.dumps(item, ensure_ascii=False),
                         item.get("created_at"), item.get("updated_at")),
                    )
                    if kind == "case" and isinstance(item.get("outfit_items"), list):
                        for item_index, part in enumerate(item["outfit_items"]):
                            if not isinstance(part, dict):
                                report["warnings"].append({
                                    "record": location, "reason": "单品记录无效，保留原始内容待审核"
                                })
                                con.execute(
                                    "INSERT INTO migration_review(id,case_id,source_index,reason) VALUES(?,?,?,?)",
                                    (_id(), asset_id, item_index, "单品记录无效"),
                                )
                                continue
                            def field(name):
                                value = part.get(name)
                                return value.strip() if isinstance(value, str) else ""
                            garment_id = _id()
                            details = part.get("details")
                            con.execute(
                                "INSERT INTO garments(id,sku,name,category,subcategory,color,silhouette,fabric,details) "
                                "VALUES(?,?,?,?,?,?,?,?,?)",
                                (garment_id, field("sku"), field("name"), field("category"),
                                 field("subcategory"), field("color"), field("silhouette"), field("material"),
                                 json.dumps(details, ensure_ascii=False) if isinstance(details, list) else ""),
                            )
                            con.execute(
                                "INSERT INTO case_garments(id,case_id,garment_id,instance_key) VALUES(?,?,?,?)",
                                (_id(), asset_id, garment_id, str(item_index)),
                            )
                            con.execute(
                                "INSERT INTO migration_review(id,case_id,source_index,reason) VALUES(?,?,?,?)",
                                (_id(), asset_id, item_index, "导入单品的身份和属性需要人工确认"),
                            )
                            if field("sku"):
                                matches = con.execute(
                                    "SELECT DISTINCT g.id FROM garments g JOIN case_garments cg ON cg.garment_id=g.id "
                                    "WHERE g.id<>? AND cg.case_id<>? AND lower(trim(g.sku))=lower(?)",
                                    (garment_id, asset_id, field("sku")),
                                ).fetchall()
                                for match in matches:
                                    first, second = sorted((garment_id, match["id"]))
                                    con.execute(
                                        "INSERT OR IGNORE INTO garment_candidates(id,first_id,second_id,reason) "
                                        "VALUES(?,?,?,?)",
                                        (_id(), first, second, "款号相同，需人工核对"),
                                    )
                    if kind == "case" and (item.get("image") or item.get("images")):
                        reason = "旧 JSON 中的图片路径尚未校验并关联，请人工审核"
                        con.execute(
                            "INSERT INTO migration_review(id,case_id,reason) VALUES(?,?,?)",
                            (_id(), asset_id, reason),
                        )
                        report["warnings"].append({"record": location, "reason": reason})
                    report["inserted"] += 1
        return report

    def import_video(self, case_id, source_path, expected_sha256):
        """Import only a previously scanned source, using an atomic content-addressed copy."""
        digest = str(expected_sha256).lower()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise StorageError("视频 SHA-256 无效")
        stage = None
        with self.store._session() as con:
            if not con.execute("SELECT 1 FROM assets WHERE id=? AND kind='case'", (case_id,)).fetchone():
                raise StorageError("关联案例不存在")
            source = self.store._verified_ingest_source_path(con, str(source_path), "video", digest)
            if source.suffix.lower() not in VIDEO_FORMATS or source.stat().st_size > MAX_VIDEO_BYTES:
                raise StorageError("视频格式或大小不受支持")
            if _verify_video_file(source) != "verified":
                raise StorageError("视频损坏或无法完成解码校验")
            refs = self.store.home / "references"
            if _is_link_or_junction(refs):
                raise StorageError("素材目录不安全")
            refs.mkdir(parents=True, exist_ok=True)
            if _is_link_or_junction(refs) or refs.resolve().parent != self.store.home.resolve():
                raise StorageError("素材目录不安全")
            name = "video_" + digest + source.suffix.lower()
            target = refs / name
            try:
                if not target.exists():
                    fd, temp_name = tempfile.mkstemp(prefix=".video-import-", suffix=source.suffix.lower(), dir=refs)
                    stage = Path(temp_name)
                    with os.fdopen(fd, "wb") as out, source.open("rb") as inp:
                        shutil.copyfileobj(inp, out, 1024 * 1024)
                        out.flush()
                        os.fsync(out.fileno())
                    if _attachment_status(refs, stage.name, "video", digest) != "verified":
                        raise StorageError("视频复制后校验失败")
                    try:
                        os.link(stage, target)
                    except FileExistsError:
                        pass
                if _attachment_status(refs, name, "video", digest) != "verified":
                    raise StorageError("已有同名素材校验失败，未覆盖")
                con.execute("INSERT OR IGNORE INTO attachments(asset_id,relative_path,media_type,role,sha256) VALUES(?,?,?,?,?)",
                            (case_id, name, "video", "reference", digest))
                return {"path": name, "sha256": digest}
            finally:
                if stage is not None:
                    stage.unlink(missing_ok=True)

    def list_garments(self, offset=0, limit=50):
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        with self.store._session() as con:
            rows = con.execute(
                "SELECT * FROM garments ORDER BY updated_at DESC,id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_garment(self, name, category, sku="", subcategory="", color="",
                       silhouette="", fabric="", details=""):
        values = (name, category, sku, subcategory, color, silhouette, fabric, details)
        if any(not isinstance(value, str) for value in values):
            raise StorageError("单品字段必须为文本")
        name, category, sku, subcategory, color, silhouette, fabric, details = (
            value.strip() for value in values
        )
        if not name or not category:
            raise StorageError("单品名称和分类不能为空")
        garment_id = _id()
        with self.store._session() as con:
            con.execute(
                "INSERT INTO garments(id,name,category,sku,subcategory,color,silhouette,fabric,details) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (garment_id, name, category, sku, subcategory, color, silhouette, fabric, details),
            )
            if sku:
                rows = con.execute(
                    "SELECT id FROM garments WHERE id<>? AND lower(trim(sku))=lower(?)",
                    (garment_id, sku),
                ).fetchall()
                for row in rows:
                    first, second = sorted((garment_id, row["id"]))
                    con.execute(
                        "INSERT INTO garment_candidates(id,first_id,second_id,reason) VALUES(?,?,?,?)",
                        (_id(), first, second, "款号相同，需人工核对"),
                    )
        return garment_id

    def update_garment(self, garment_id, values):
        permitted = {"name", "category", "sku", "subcategory", "color", "silhouette", "fabric", "details"}
        if not isinstance(values, dict) or set(values) - permitted:
            raise StorageError("单品字段无效")
        with self.store._session() as con:
            current = con.execute("SELECT * FROM garments WHERE id=?", (garment_id,)).fetchone()
            if current is None:
                raise StorageError("单品不存在")
            updated = dict(current)
            for key, value in values.items():
                if not isinstance(value, str):
                    raise StorageError("单品字段必须为文本")
                updated[key] = value.strip()
            if not updated["name"] or not updated["category"]:
                raise StorageError("单品名称和分类不能为空")
            con.execute(
                "INSERT INTO garment_revisions(id,garment_id,payload_json) VALUES(?,?,?)",
                (_id(), garment_id, json.dumps(dict(current), ensure_ascii=False)),
            )
            con.execute(
                "UPDATE garments SET name=?,category=?,sku=?,subcategory=?,color=?,silhouette=?,"
                "fabric=?,details=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                tuple(updated[key] for key in ("name","category","sku","subcategory","color","silhouette","fabric","details"))
                + (garment_id,),
            )
            if updated["sku"]:
                matches = con.execute(
                    "SELECT id FROM garments WHERE id<>? AND lower(trim(sku))=lower(?)",
                    (garment_id, updated["sku"]),
                ).fetchall()
                for row in matches:
                    first, second = sorted((garment_id, row["id"]))
                    con.execute(
                        "INSERT OR IGNORE INTO garment_candidates(id,first_id,second_id,reason) VALUES(?,?,?,?)",
                        (_id(), first, second, "款号相同，需人工核对"),
                    )

    def garment_detail(self, garment_id):
        with self.store._session() as con:
            garment = con.execute("SELECT * FROM garments WHERE id=?", (garment_id,)).fetchone()
            if garment is None:
                raise StorageError("单品不存在")
            cases = con.execute(
                "SELECT * FROM case_garments WHERE garment_id=? ORDER BY case_id,instance_key",
                (garment_id,),
            ).fetchall()
            return {"garment": dict(garment), "cases": [dict(row) for row in cases]}

    def link_case(self, case_id, garment_id, instance_key="", role=""):
        case_id, garment_id = str(case_id), str(garment_id)
        with self.store._session() as con:
            case = con.execute("SELECT kind FROM assets WHERE id=?", (case_id,)).fetchone()
            if case is None or case["kind"] != "case":
                raise StorageError("案例不存在")
            existing = con.execute(
                "SELECT id FROM case_garments WHERE case_id=? AND garment_id=? AND instance_key=?",
                (case_id, garment_id, str(instance_key)),
            ).fetchone()
            if existing:
                return existing["id"]
            link_id = _id()
            con.execute(
                "INSERT INTO case_garments(id,case_id,garment_id,instance_key,role) VALUES(?,?,?,?,?)",
                (link_id, case_id, garment_id, str(instance_key), str(role)),
            )
            return link_id

    def set_case_status(self, link_id, status, review_status):
        if status not in {"pending", "success", "partial", "failed"}:
            raise StorageError("单品状态无效")
        if review_status not in {"pending", "approved", "rejected"}:
            raise StorageError("审核状态无效")
        with self.store._session() as con:
            result = con.execute(
                "UPDATE case_garments SET status=?,review_status=? WHERE id=?",
                (status, review_status, link_id),
            )
            if result.rowcount != 1:
                raise StorageError("案例单品关联不存在")

    def list_sources(self, case_id, offset=0, limit=50):
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        with self.store._session() as con:
            rows = con.execute(
                "SELECT id,case_id,version,body,sha256,revision_id,created_at "
                "FROM prompt_sources WHERE case_id=? ORDER BY version DESC LIMIT ? OFFSET ?",
                (case_id, limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_reviews(self, offset=0, limit=50):
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        with self.store._session() as con:
            rows = con.execute(
                "SELECT * FROM migration_review WHERE status='pending' "
                "ORDER BY case_id,source_index,id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_candidates(self, offset=0, limit=50):
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        with self.store._session() as con:
            rows = con.execute(
                "SELECT * FROM garment_candidates WHERE review_status='pending' "
                "ORDER BY id LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
            return [dict(row) for row in rows]

    def resolve_review(self, review_id, decision, candidate=False):
        table = "garment_candidates" if candidate else "migration_review"
        column = "review_status" if candidate else "status"
        allowed = {"approved", "rejected"} if candidate else {"resolved", "rejected"}
        if decision not in allowed:
            raise StorageError("审核结果无效")
        with self.store._session() as con:
            result = con.execute(
                "UPDATE " + table + " SET " + column + "=? WHERE id=? AND " + column + "='pending'",
                (decision, review_id),
            )
            if result.rowcount != 1:
                raise StorageError("审核记录不存在或已处理")

    def list_attachments(self, case_id, offset=0, limit=50):
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        with self.store._session() as con:
            rows = con.execute(
                "SELECT id,relative_path,media_type,role FROM attachments WHERE asset_id=? "
                "ORDER BY id LIMIT ? OFFSET ?", (case_id, limit, offset)
            ).fetchall()
            return [dict(row) for row in rows]

    def list_ps_notes(self, case_id, offset=0, limit=50):
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        with self.store._session() as con:
            rows = con.execute(
                "SELECT * FROM ps_notes WHERE case_id=? ORDER BY id LIMIT ? OFFSET ?",
                (case_id, limit, offset),
            ).fetchall()
            notes = []
            for row in rows:
                note = dict(row)
                assets = con.execute(
                    "SELECT p.attachment_id,p.role,a.relative_path,a.media_type "
                    "FROM ps_note_assets p JOIN attachments a ON a.id=p.attachment_id "
                    "WHERE p.note_id=? ORDER BY p.position,a.id", (note["id"],)
                ).fetchall()
                note["assets"] = [dict(asset) for asset in assets]
                notes.append(note)
            return notes

    def append_source(self, case_id, body):
        if not isinstance(body, str):
            raise StorageError("原始提示词必须为文本")
        with self.store._session() as con:
            case = con.execute("SELECT kind FROM assets WHERE id=?", (case_id,)).fetchone()
            if case is None or case["kind"] != "case":
                raise StorageError("案例不存在")
            version = con.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM prompt_sources WHERE case_id=?",
                (case_id,),
            ).fetchone()[0]
            source_id = _id()
            con.execute("INSERT INTO prompt_sources(id,case_id,version,body,sha256) VALUES(?,?,?,?,?)",
                        (source_id, case_id, version, body, hashlib.sha256(body.encode("utf-8")).hexdigest()))
            return source_id

    def add_segment(self, source_id, dimension, start, end, case_garment_id=None):
        with self.store._session() as con:
            source = con.execute("SELECT case_id,body FROM prompt_sources WHERE id=?", (source_id,)).fetchone()
            if source is None:
                raise StorageError("提示词版本不存在")
            if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(source["body"]):
                raise StorageError("原文区间无效")
            if case_garment_id is not None:
                link = con.execute("SELECT case_id FROM case_garments WHERE id=?", (case_garment_id,)).fetchone()
                if link is None or link["case_id"] != source["case_id"]:
                    raise StorageError("单品和原文不属于同一案例")
            segment_id = _id()
            con.execute(
                "INSERT INTO prompt_segments(id,source_id,case_garment_id,dimension,original_text,source_start,source_end) VALUES(?,?,?,?,?,?,?)",
                (segment_id, source_id, case_garment_id, dimension, source["body"][start:end], start, end),
            )
            return segment_id

    def add_segment_reference(self, segment_id, attachment_id, marker):
        marker = str(marker).strip()
        if not marker:
            raise StorageError("参考素材标记不能为空")
        with self.store._session() as con:
            row = con.execute(
                "SELECT p.case_id,p.body,a.asset_id FROM prompt_segments s "
                "JOIN prompt_sources p ON p.id=s.source_id "
                "JOIN attachments a ON a.id=? WHERE s.id=?",
                (attachment_id, segment_id),
            ).fetchone()
            if row is None or row["case_id"] != row["asset_id"]:
                raise StorageError("素材与片段不属于同一案例")
            if marker not in row["body"]:
                raise StorageError("原文中不存在此参考标记")
            ref_id = _id()
            con.execute(
                "INSERT INTO prompt_segment_refs(id,segment_id,attachment_id,marker) VALUES(?,?,?,?)",
                (ref_id, segment_id, attachment_id, marker),
            )
            return ref_id

    def add_ps_asset(self, note_id, attachment_id, role):
        if role not in {"before", "after"}:
            raise StorageError("图片角色无效")
        with self.store._session() as con:
            row = con.execute(
                "SELECT p.case_id,a.asset_id FROM ps_notes p "
                "JOIN attachments a ON a.id=? WHERE p.id=?",
                (attachment_id, note_id),
            ).fetchone()
            if row is None or row["case_id"] != row["asset_id"]:
                raise StorageError("素材与修改记录不属于同一案例")
            con.execute(
                "INSERT INTO ps_note_assets(note_id,attachment_id,role) VALUES(?,?,?)",
                (note_id, attachment_id, role),
            )

    def add_ps_note(self, case_id, area, problem, case_garment_id=None,
                    desired="", operation="", suggestion="", before_id=None, after_id=None):
        if not str(area).strip() or not str(problem).strip():
            raise StorageError("修改区域和原始问题不能为空")
        with self.store._session() as con:
            case = con.execute("SELECT kind FROM assets WHERE id=?", (case_id,)).fetchone()
            if case is None or case["kind"] != "case":
                raise StorageError("案例不存在")
            if case_garment_id is not None:
                link = con.execute("SELECT case_id FROM case_garments WHERE id=?", (case_garment_id,)).fetchone()
                if link is None or link["case_id"] != case_id:
                    raise StorageError("单品和修改记录不属于同一案例")
            for attachment_id in (before_id, after_id):
                if attachment_id is not None:
                    asset = con.execute(
                        "SELECT asset_id FROM attachments WHERE id=?", (attachment_id,)
                    ).fetchone()
                    if asset is None or asset["asset_id"] != case_id:
                        raise StorageError("修改前后素材必须属于该案例")
            note_id = _id()
            con.execute(
                "INSERT INTO ps_notes(id,case_id,case_garment_id,area,problem,desired,operation,suggestion) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (note_id, case_id, case_garment_id, str(area), str(problem),
                 str(desired), str(operation), str(suggestion)),
            )
            for role, attachment_id in (("before", before_id), ("after", after_id)):
                if attachment_id is not None:
                    con.execute(
                        "INSERT INTO ps_note_assets(note_id,attachment_id,role) VALUES(?,?,?)",
                        (note_id, attachment_id, role),
                    )
            return note_id


def inspect_database(database, references):
    database = Path(database).resolve(strict=True)
    references = Path(references)
    con = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        quick = [row[0] for row in con.execute("PRAGMA quick_check")]
        integrity = [row[0] for row in con.execute("PRAGMA integrity_check")]
        foreign_keys = [tuple(row) for row in con.execute("PRAGMA foreign_key_check")]
        tables = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if not {"metadata", "assets", "attachments"}.issubset(tables):
            raise ValueError("数据库缺少必要的现有表")
        version_row = con.execute(
            "SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        version = version_row[0] if version_row else None
        legacy_tables = {"metadata", "assets", "asset_revisions", "attachments", "scan_sources",
                         "ingest_files", "generation_feedback", "retrieval_feedback", "ai_analyses"}
        fingerprint = hashlib.sha256()
        for table in sorted((legacy_tables - {"attachments"}) & tables):
            fingerprint.update((table + "\n").encode("utf-8"))
            rows = con.execute('SELECT * FROM "' + table + '" ORDER BY rowid')
            for record in rows:
                if table == "metadata" and record["key"] == "schema_version":
                    continue
                values = [({"bytes": value.hex()} if isinstance(value, bytes) else value)
                          for value in record]
                fingerprint.update(json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                fingerprint.update(b"\n")
        schema_errors = []
        missing_legacy = sorted(legacy_tables - tables)
        if missing_legacy:
            schema_errors.append("数据库缺少旧版表：" + ", ".join(missing_legacy))
        expected_columns = {
            "metadata": {"key", "value"},
            "assets": {"id", "kind", "payload_json", "created_at", "updated_at"},
            "attachments": {"id", "asset_id", "relative_path", "media_type", "sha256"},
        }
        for table, columns in expected_columns.items():
            actual = {row[1] for row in con.execute('PRAGMA table_info("' + table + '")')}
            if not columns.issubset(actual):
                schema_errors.append(table + " 表缺少必要字段")
        if version not in {"1", "2"}:
            schema_errors.append("未知数据库版本")
        if version == "2":
            required_v2 = {"case_tombstones", "garments", "garment_revisions", "case_garments", "prompt_sources", "prompt_segments",
                           "prompt_segment_refs", "ps_notes", "ps_note_assets", "garment_candidates"}
            missing = sorted(required_v2 - tables)
            if missing:
                schema_errors.append("V2 数据库缺少表：" + ", ".join(missing))
        counts = {name: con.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
                  for name in sorted(tables)}
        kind_counts = {row["kind"]: row["total"] for row in con.execute(
            "SELECT kind, COUNT(*) AS total FROM assets GROUP BY kind")}
        prompt_hashes = []
        payload_references = []
        for row in con.execute("SELECT id, payload_json FROM assets ORDER BY id"):
            payload = json.loads(row["payload_json"])
            body = payload.get("body")
            if isinstance(body, str):
                prompt_hashes.append({"asset_id": row["id"],
                                      "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()})
            raw_images = payload.get("images")
            names = list(raw_images) if isinstance(raw_images, list) else []
            if payload.get("image"):
                names.append(payload["image"])
            seen = set()
            for name in names:
                if not isinstance(name, str) or not name.strip():
                    payload_references.append({"asset_id": row["id"], "relative_path": "",
                                               "sha256": None, "status": "unsupported"})
                    continue
                if name in seen:
                    continue
                seen.add(name)
                computed = {}
                status = _attachment_status(references, name, "image", None, computed, True)
                payload_references.append({"asset_id": row["id"], "relative_path": name,
                                           "sha256": computed.get("sha256"), "status": status})
        revision_prompt_hashes = []
        if "asset_revisions" in tables:
            for row in con.execute("SELECT revision_id, asset_id, payload_json FROM asset_revisions ORDER BY revision_id"):
                payload = json.loads(row["payload_json"])
                body = payload.get("body")
                if isinstance(body, str):
                    revision_prompt_hashes.append({
                        "revision_id": row["revision_id"], "asset_id": row["asset_id"],
                        "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                    })
        attachments = []
        unresolved_video_ids = ([row[0] for row in con.execute(
            "SELECT attachment_id FROM unresolved_video_refs WHERE status='pending'")]
            if "unresolved_video_refs" in tables else [])
        for row in con.execute(
            "SELECT id, relative_path, media_type, sha256 FROM attachments ORDER BY id"
        ):
            status = _attachment_status(references, row["relative_path"], row["media_type"], row["sha256"])
            attachments.append({"id": row["id"], "relative_path": row["relative_path"],
                                "media_type": row["media_type"], "sha256": row["sha256"], "status": status})
        return {"schema_version": version, "schema_errors": schema_errors,
                "legacy_fingerprint": fingerprint.hexdigest(),
                "quick_check": quick, "integrity": integrity,
                "foreign_key_errors": foreign_keys, "counts": counts, "kind_counts": kind_counts,
                "prompt_hashes": prompt_hashes, "revision_prompt_hashes": revision_prompt_hashes,
                "attachments": attachments, "payload_references": payload_references,
                "unresolved_video_ids": unresolved_video_ids}
    finally:
        con.close()


DIMENSIONS = {
    "整体外观": "overall_appearance",
    "版型与廓形": "silhouette",
    "结构细节": "construction",
    "面料材质": "fabric",
    "表面效果": "surface",
    "上身效果": "fit_on_body",
    "反光效果": "reflectivity",
    "特殊设计": "special_design",
    "模型约束": "model_constraints",
    "禁止事项": "prohibitions",
}


def map_dimension(label):
    """Map an unambiguous semantic label; numbers alone require review."""
    return DIMENSIONS.get(str(label).strip())


SCHEMA = [
    """CREATE TABLE case_tombstones (
        case_id TEXT PRIMARY KEY REFERENCES assets(id),
        deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE garments (
        id TEXT PRIMARY KEY, sku TEXT NOT NULL DEFAULT '', name TEXT NOT NULL,
        category TEXT NOT NULL, subcategory TEXT NOT NULL DEFAULT '',
        color TEXT NOT NULL DEFAULT '', silhouette TEXT NOT NULL DEFAULT '',
        fabric TEXT NOT NULL DEFAULT '', details TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE garment_revisions (
        id TEXT PRIMARY KEY, garment_id TEXT NOT NULL REFERENCES garments(id),
        payload_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE case_garments (
        id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES assets(id),
        garment_id TEXT NOT NULL REFERENCES garments(id), instance_key TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending','success','partial','failed')),
        review_status TEXT NOT NULL DEFAULT 'pending'
            CHECK(review_status IN ('pending','approved','rejected')),
        final_result TEXT NOT NULL DEFAULT '', verified_at TEXT,
        UNIQUE(case_id, garment_id, instance_key))""",
    """CREATE TABLE prompt_sources (
        id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES assets(id),
        version INTEGER NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL,
        revision_id TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(case_id, version))""",
    """CREATE TRIGGER assets_capture_prompt_insert AFTER INSERT ON assets
       WHEN NEW.kind='case' AND json_type(NEW.payload_json,'$.body')='text'
       BEGIN
         INSERT INTO prompt_sources(id,case_id,version,body,sha256)
         VALUES(lower(hex(randomblob(16))),NEW.id,1,
                json_extract(NEW.payload_json,'$.body'),
                prompt_atelier_sha256(json_extract(NEW.payload_json,'$.body')));
       END""",
    """CREATE TRIGGER assets_capture_prompt_update AFTER UPDATE OF payload_json ON assets
       WHEN NEW.kind='case' AND json_type(NEW.payload_json,'$.body')='text'
         AND NOT EXISTS (
           SELECT 1 FROM prompt_sources WHERE case_id=NEW.id
             AND version=(SELECT MAX(version) FROM prompt_sources WHERE case_id=NEW.id)
             AND body=json_extract(NEW.payload_json,'$.body')
         )
       BEGIN
         INSERT INTO prompt_sources(id,case_id,version,body,sha256)
         VALUES(lower(hex(randomblob(16))),NEW.id,
                (SELECT COALESCE(MAX(version),0)+1 FROM prompt_sources WHERE case_id=NEW.id),
                json_extract(NEW.payload_json,'$.body'),
                prompt_atelier_sha256(json_extract(NEW.payload_json,'$.body')));
       END""",
    """CREATE TRIGGER prompt_sources_no_update BEFORE UPDATE ON prompt_sources
       BEGIN SELECT RAISE(ABORT, 'prompt source is immutable'); END""",
    """CREATE TRIGGER prompt_sources_no_delete BEFORE DELETE ON prompt_sources
       BEGIN SELECT RAISE(ABORT, 'prompt source is immutable'); END""",
    """CREATE TABLE prompt_segments (
        id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES prompt_sources(id),
        case_garment_id TEXT REFERENCES case_garments(id), dimension TEXT NOT NULL,
        original_text TEXT NOT NULL, normalized_text TEXT NOT NULL DEFAULT '',
        source_start INTEGER NOT NULL, source_end INTEGER NOT NULL,
        confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1),
        review_status TEXT NOT NULL DEFAULT 'pending'
            CHECK(review_status IN ('pending','approved','rejected')),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK(source_start >= 0 AND source_end >= source_start))""",
    """CREATE TRIGGER prompt_segment_validate_insert BEFORE INSERT ON prompt_segments
       WHEN NEW.dimension NOT IN ('overall_appearance','silhouette','construction','fabric','surface',
         'fit_on_body','reflectivity','special_design','model_constraints','prohibitions')
         OR (SELECT substr(body, NEW.source_start + 1, NEW.source_end - NEW.source_start)
             FROM prompt_sources WHERE id = NEW.source_id) IS NOT NEW.original_text
       BEGIN SELECT RAISE(ABORT, 'segment must match source text and dimension'); END""",
    """CREATE TRIGGER prompt_segment_validate_update BEFORE UPDATE ON prompt_segments
       WHEN NEW.dimension NOT IN ('overall_appearance','silhouette','construction','fabric','surface',
         'fit_on_body','reflectivity','special_design','model_constraints','prohibitions')
         OR (SELECT substr(body, NEW.source_start + 1, NEW.source_end - NEW.source_start)
             FROM prompt_sources WHERE id = NEW.source_id) IS NOT NEW.original_text
       BEGIN SELECT RAISE(ABORT, 'segment must match source text and dimension'); END""",
    """CREATE TABLE prompt_segment_refs (
        id TEXT PRIMARY KEY, segment_id TEXT NOT NULL REFERENCES prompt_segments(id),
        attachment_id INTEGER REFERENCES attachments(id), marker TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT '', review_status TEXT NOT NULL DEFAULT 'pending',
        UNIQUE(segment_id, attachment_id, marker))""",
    """CREATE TABLE ps_notes (
        id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES assets(id),
        case_garment_id TEXT REFERENCES case_garments(id), area TEXT NOT NULL,
        problem TEXT NOT NULL, desired TEXT NOT NULL DEFAULT '',
        operation TEXT NOT NULL DEFAULT '', suggestion TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending', verified_at TEXT)""",
    """CREATE TABLE ps_note_assets (
        note_id TEXT NOT NULL REFERENCES ps_notes(id),
        attachment_id INTEGER NOT NULL REFERENCES attachments(id),
        role TEXT NOT NULL CHECK(role IN ('before','after')), position INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(note_id, attachment_id, role))""",
    """CREATE TABLE migration_review (
        id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES assets(id),
        source_index INTEGER, reason TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending','resolved','rejected')))""",
    """CREATE TABLE unresolved_video_refs (
        attachment_id INTEGER PRIMARY KEY REFERENCES attachments(id),
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending')))""",
    """CREATE TABLE garment_candidates (
        id TEXT PRIMARY KEY, first_id TEXT NOT NULL REFERENCES garments(id),
        second_id TEXT NOT NULL REFERENCES garments(id), reason TEXT NOT NULL,
        review_status TEXT NOT NULL DEFAULT 'pending'
            CHECK(review_status IN ('pending','approved','rejected')),
        CHECK(first_id < second_id), UNIQUE(first_id, second_id))""",
    *[
        f"""CREATE TRIGGER stage2_guard_{table}_{operation.lower()}
            BEFORE {operation} ON {table}
            WHEN prompt_atelier_stage2_write() != 1
            BEGIN SELECT RAISE(ABORT, 'newer database requires updated application'); END"""
        for table in ("assets", "asset_revisions", "attachments")
        for operation in ("INSERT", "UPDATE", "DELETE")
    ],
    "CREATE INDEX idx_case_garments_garment ON case_garments(garment_id)",
    "CREATE INDEX idx_prompt_segments_source ON prompt_segments(source_id)",
    "CREATE INDEX idx_prompt_segment_refs_attachment ON prompt_segment_refs(attachment_id)",
    "CREATE INDEX idx_ps_notes_case ON ps_notes(case_id)",
]


def migrate(store, _protected_stage=False):
    """Migrate an isolated or explicitly created protected staging store."""
    temp_root = Path(tempfile.gettempdir()).resolve()
    protected = _protected_stage and store.home.name.startswith(".stage2-copy-")
    if not store.home.resolve().is_relative_to(temp_root) and not protected:
        raise StorageError("迁移仅允许对受保护暂存副本操作")
    with store.exclusive_operation():
        before = inspect_database(store.db_path, store.home / "references")
        if before["schema_version"] not in {"1", "2"} or (before["integrity"] != ["ok"] or before["quick_check"] != ["ok"]):
            raise StorageError("数据库版本或完整性检查失败")
        if before["schema_errors"] or before["foreign_key_errors"] or _blocking_preflight_items(before):
            raise StorageError("附件或外键检查失败，迁移已中止")
        if before["schema_version"] == "2":
            return {"status": "already_migrated", "before": before}
        backup = store.create_full_backup(store.home / "references", app_version="pre-stage2")
        with store._session() as con:
            con.execute("BEGIN IMMEDIATE")
            for statement in SCHEMA:
                con.execute(statement)
            for item in before["attachments"]:
                if _is_legacy_absolute_video(item):
                    con.execute("INSERT INTO unresolved_video_refs(attachment_id) VALUES(?)", (item["id"],))
                    owner = con.execute("SELECT asset_id FROM attachments WHERE id=?", (item["id"],)).fetchone()
                    con.execute("INSERT INTO migration_review(id,case_id,reason) VALUES(?,?,?)",
                                (uuid.uuid4().hex, owner[0], "历史视频路径未授权，需重新定位"))
            rows = con.execute(
                "SELECT id, payload_json FROM assets WHERE kind='case' ORDER BY id").fetchall()
            for row in rows:
                payload = json.loads(row["payload_json"])
                versions = []
                revisions = con.execute(
                    "SELECT revision_id,payload_json FROM asset_revisions "
                    "WHERE asset_id=? AND kind='case' ORDER BY created_at,rowid",
                    (row["id"],),
                ).fetchall()
                for revision in revisions:
                    previous = json.loads(revision["payload_json"])
                    if isinstance(previous.get("body"), str):
                        versions.append((previous["body"], revision["revision_id"]))
                if isinstance(payload.get("body"), str):
                    versions.append((payload["body"], None))
                for number, (body, revision_id) in enumerate(versions, 1):
                    con.execute(
                        "INSERT INTO prompt_sources(id,case_id,version,body,sha256,revision_id) "
                        "VALUES(?,?,?,?,?,?)",
                        (uuid.uuid4().hex, row["id"], number, body,
                         hashlib.sha256(body.encode("utf-8")).hexdigest(), revision_id),
                    )
                items = payload.get("outfit_items")
                if not isinstance(items, list):
                    if items is not None:
                        con.execute(
                            "INSERT INTO migration_review(id,case_id,reason) VALUES(?,?,?)",
                            (uuid.uuid4().hex, row["id"], "单品结构无效"),
                        )
                    continue
                for index, item in enumerate(items):
                    if not isinstance(item, dict):
                        con.execute(
                            "INSERT INTO migration_review(id,case_id,source_index,reason) VALUES(?,?,?,?)",
                            (uuid.uuid4().hex, row["id"], index, "单品记录无效"),
                        )
                        continue
                    def text_field(name):
                        value = item.get(name)
                        return value.strip() if isinstance(value, str) else ""
                    garment_id = uuid.uuid4().hex
                    details = item.get("details")
                    con.execute(
                        "INSERT INTO garments(id,sku,name,category,subcategory,color,silhouette,fabric,details) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (garment_id, text_field("sku"), text_field("name"),
                         text_field("category"), text_field("subcategory"), text_field("color"),
                         text_field("silhouette"), text_field("material"),
                         json.dumps(details, ensure_ascii=False) if isinstance(details, list) else ""),
                    )
                    con.execute(
                        "INSERT INTO case_garments(id,case_id,garment_id,instance_key) VALUES(?,?,?,?)",
                        (uuid.uuid4().hex, row["id"], garment_id, str(index)),
                    )
                    missing = [key for key in ("sku", "name", "category", "color", "material")
                               if not text_field(key)]
                    reason = "单品来源及属性需人工确认"
                    if missing:
                        reason += "；缺少 " + ", ".join(missing)
                    con.execute(
                        "INSERT INTO migration_review(id,case_id,source_index,reason) VALUES(?,?,?,?)",
                        (uuid.uuid4().hex, row["id"], index, reason),
                    )
            candidates = con.execute(
                "SELECT a.id,b.id,a.sku FROM garments a "
                "JOIN case_garments ca ON ca.garment_id=a.id "
                "JOIN garments b ON a.id < b.id AND trim(a.sku) <> '' "
                "AND lower(trim(a.sku)) = lower(trim(b.sku)) "
                "JOIN case_garments cb ON cb.garment_id=b.id AND cb.case_id<>ca.case_id"
            ).fetchall()
            for candidate in candidates:
                con.execute(
                    "INSERT INTO garment_candidates(id,first_id,second_id,reason) VALUES(?,?,?,?)",
                    (uuid.uuid4().hex, candidate[0], candidate[1], "款号相同，需人工核对"),
                )
            for ref in before["payload_references"]:
                exists = con.execute(
                    "SELECT 1 FROM attachments WHERE asset_id=? AND relative_path=?",
                    (ref["asset_id"], ref["relative_path"]),
                ).fetchone()
                if not exists:
                    con.execute(
                        "INSERT INTO attachments(asset_id,relative_path,media_type,role,sha256) VALUES(?,?,?,?,?)",
                        (ref["asset_id"], ref["relative_path"], "image", "reference", ref["sha256"]),
                    )
            con.execute("UPDATE metadata SET value='2' WHERE key='schema_version'")
            if con.execute("PRAGMA foreign_key_check").fetchall():
                raise StorageError("迁移后外键检查失败")
        return {"status": "migrated", "backup": str(backup), "before": before}


def _write_ready_marker(destination):
    """Publish the completion marker only after its bytes are durable."""
    fd, temp_name = tempfile.mkstemp(prefix=".ready-", dir=destination)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write('{"schema_version":2}')
            stream.flush()
            os.fsync(stream.fileno())
        marker = destination / "stage2-ready.json"
        if os.name == "nt":
            os.rename(temp, marker)
        else:
            os.link(temp, marker)
    finally:
        if temp.exists():
            temp.unlink()


def create_protected_copy(source, destination):
    """Copy an isolated V1 store into a separate V2 directory without replacing V1."""
    destination = Path(destination)
    temp_root = Path(tempfile.gettempdir()).resolve()
    if not source.home.resolve().is_relative_to(temp_root):
        expected = Path.home() / "AI_Prompt_Asset_Workbench"
        expected_destination = Path.home() / "AI_Prompt_Asset_Workbench_v2"
        if source.home.resolve() != expected.resolve() or destination.resolve() != expected_destination.resolve():
            raise StorageError("正式迁移只允许从默认目录创建受保护副本")
    if _is_link_or_junction(source.home):
        raise StorageError("来源目录不能是链接")
    if destination.exists() or destination.parent.resolve() != source.home.resolve().parent:
        raise StorageError("目标必须是与来源同级且不存在的目录")
    with _activation_lock(source.home), source.exclusive_operation(), source._session() as source_guard:
        source_guard.execute("BEGIN IMMEDIATE")
        report = inspect_database(source.db_path, source.home / "references")
        if (report["schema_version"] != "1" or report["schema_errors"]
                or report["quick_check"] != ["ok"] or report["integrity"] != ["ok"]
                or report["foreign_key_errors"]
                or _blocking_preflight_items(report)):
            raise StorageError("来源预检失败")
        backup = source.create_full_backup(source.home / "references", app_version="pre-stage2")
        work = Path(tempfile.mkdtemp(prefix=".stage2-copy-", dir=destination.parent))
        target = Storage(work)
        target.restore_full_backup(backup)
        migration = migrate(target, _protected_stage=True)
        after = inspect_database(target.db_path, work / "references")
        if (after["schema_version"] != "2" or after["schema_errors"]
                or after["quick_check"] != ["ok"] or after["integrity"] != ["ok"]
                or after["foreign_key_errors"] or after["counts"]["assets"] != report["counts"]["assets"]
                or after["prompt_hashes"] != report["prompt_hashes"]
                or after["legacy_fingerprint"] != report["legacy_fingerprint"]
                or any(item not in after["attachments"] for item in report["attachments"])
                or after["payload_references"] != report["payload_references"]):
            raise StorageError(f"隔离复制校验失败，副本保留在 {work}")
        current = inspect_database(source.db_path, source.home / "references")
        if (current["legacy_fingerprint"] != report["legacy_fingerprint"]
                or current["attachments"] != report["attachments"]
                or current["payload_references"] != report["payload_references"]):
            raise StorageError("迁移期间来源数据发生变化，已保留暂存副本并中止")
        origin = {
            "source_home": str(source.home.resolve()),
            "destination": str(destination.resolve()),
            "legacy_fingerprint": report["legacy_fingerprint"],
            "attachments": report["attachments"],
            "payload_references": report["payload_references"],
        }
        with (work / "stage2-origin.json").open("x", encoding="utf-8") as stream:
            json.dump(origin, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())

        # Reserve a previously absent container, preventing a competing process
        # from replacing any existing directory. A missing marker means recovery
        # must be reviewed before the application uses this location.
        destination.mkdir()
        data_dir = destination / "data"
        os.rename(work, data_dir)
        _write_ready_marker(destination)
        migration["backup"] = str(data_dir / Path(migration["backup"]).relative_to(work))
        return {"status": "created", "source_backup": str(backup),
                "destination": str(data_dir), "migration": migration, "report": after}


def recover_protected_copy(destination, staged_copy):
    """Finish an interrupted test activation without overwriting any directory."""
    destination = Path(destination)
    staged_copy = Path(staged_copy)
    temp_root = Path(tempfile.gettempdir()).resolve()
    if not destination.resolve().is_relative_to(temp_root):
        expected = Path.home() / "AI_Prompt_Asset_Workbench_v2"
        if destination.resolve() != expected.resolve():
            raise StorageError("正式恢复仅支持默认 V2 数据目录")
    if (not destination.is_dir() or (destination / "stage2-ready.json").exists()
            or destination.is_symlink() or staged_copy.parent.resolve() != destination.parent.resolve()
            or not staged_copy.name.startswith(".stage2-copy-") or staged_copy.is_symlink()):
        raise StorageError("恢复目录无效或已启用")
    data = destination / "data"
    if data.exists() and staged_copy.exists():
        raise StorageError("目标和暂存目录均有数据，拒绝覆盖")
    candidate = data if data.is_dir() else staged_copy
    if candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
        raise StorageError("恢复数据不能是链接")
    origin_file = candidate / "stage2-origin.json"
    if _is_link_or_junction(origin_file) or not origin_file.is_file():
        raise StorageError("恢复副本缺少来源记录")
    try:
        origin = json.loads(origin_file.read_text(encoding="utf-8"))
        source_home = Path(origin["source_home"])
        if (source_home.parent.resolve() != destination.parent.resolve()
                or source_home == destination or _is_link_or_junction(source_home)
                or Path(origin["destination"]) != destination.resolve()):
            raise ValueError("来源位置不一致")
        if (not destination.resolve().is_relative_to(temp_root)
                and source_home.resolve() != (Path.home() / "AI_Prompt_Asset_Workbench").resolve()):
            raise ValueError("恢复来源不属于默认旧版目录")
        source_db = source_home / "prompt_library.sqlite3"
        if _is_link_or_junction(source_db) or not source_db.is_file():
            raise ValueError("恢复来源数据库无效")
        with _activation_lock(source_home):
            return _finish_protected_recovery(source_db, source_home, origin, candidate, staged_copy, data, destination)
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        raise StorageError("恢复来源验证失败，已保留所有数据") from exc


def _finish_protected_recovery(source_db, source_home, origin, candidate, staged_copy, data, destination):
    guard = sqlite3.connect(source_db, timeout=15)
    try:
        guard.execute("BEGIN IMMEDIATE")
        source_report = inspect_database(source_db, source_home / "references")
        report = inspect_database(candidate / "prompt_library.sqlite3", candidate / "references")
        if (source_report["legacy_fingerprint"] != origin["legacy_fingerprint"]
                or source_report["attachments"] != origin["attachments"]
                or source_report["payload_references"] != origin["payload_references"]):
            raise StorageError("来源数据在迁移中断后已变化，请保留两份数据并人工处理")
        if (report["schema_version"] != "2" or report["schema_errors"]
                or report["quick_check"] != ["ok"] or report["integrity"] != ["ok"]
                or report["foreign_key_errors"]
                or report["legacy_fingerprint"] != origin["legacy_fingerprint"]
                or any(item not in report["attachments"] for item in origin["attachments"])
                or report["payload_references"] != origin["payload_references"]
                or _blocking_preflight_items(report)):
            raise StorageError("隔离副本未通过恢复校验")
        if candidate == staged_copy:
            os.rename(staged_copy, data)
        _write_ready_marker(destination)
    finally:
        guard.rollback()
        guard.close()
    return {"status": "recovered", "destination": str(data)}
