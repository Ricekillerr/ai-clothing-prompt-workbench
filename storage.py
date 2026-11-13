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
import tempfile
import threading
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = 1


def _is_link_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    checker = getattr(path, "is_junction", None)
    try:
        return bool(checker()) if callable(checker) else False
    except OSError:
        return True


class StorageError(RuntimeError):
    pass


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
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=15)
        con.row_factory = sqlite3.Row
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
                    (str(SCHEMA_VERSION),),
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
                rows = con.execute(
                    "SELECT kind,payload_json FROM assets ORDER BY updated_at DESC, rowid DESC"
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
                             WHERE asset_id=? ORDER BY created_at DESC LIMIT 50
                           )""",
                        (asset_id, asset_id),
                    )

                for key, kind in (("cases", "case"), ("modules", "module")):
                    for item in data[key]:
                        asset_id = str(item["id"])
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

                existing_rows = con.execute("SELECT id,kind,payload_json FROM assets").fetchall()
                removed_rows = [row for row in existing_rows if row["id"] not in incoming_ids]
                for row in removed_rows:
                    retain_revision(row["id"], row["kind"], row["payload_json"], "deleted")
                    con.execute("DELETE FROM assets WHERE id=?", (row["id"],))
        except Exception as exc:
            raise StorageError(f"保存失败，事务已回滚：{exc}") from exc


    def list_asset_revisions(self, asset_id: str, limit: int = 50) -> list[dict]:
        asset_id = str(asset_id or "").strip()
        if not asset_id:
            raise StorageError("资产 ID 不能为空")
        limit = min(50, max(1, int(limit)))
        with self._session() as con:
            rows = con.execute(
                """SELECT revision_id,asset_id,kind,reason,created_at,payload_json
                   FROM asset_revisions WHERE asset_id=?
                   ORDER BY created_at DESC LIMIT ?""",
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

    def restore_asset_revision(self, asset_id: str, revision_id: str) -> dict:
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
            if current:
                con.execute(
                    """INSERT INTO asset_revisions
                       (revision_id,asset_id,kind,payload_json,reason,created_at)
                       VALUES(?,?,?,?,?,?)""",
                    (uuid.uuid4().hex, asset_id, current["kind"], current["payload_json"],
                     "before_restore", now),
                )
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
            prompt_text = result.get("prompt_text")
            if not isinstance(prompt_text, str) or not prompt_text.strip():
                raise StorageError("分析结果缺少原始提示词，不能入库")

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
                asset_file = Path(str(raw_path)).resolve()
                if not asset_file.is_file():
                    raise StorageError(f"关联素材已不存在，不能入库：{asset_file.name}")
                digest = hashlib.sha256()
                with asset_file.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != str(expected_hash):
                    raise StorageError(f"关联素材在 AI 分析后发生变化，请重新扫描并分析：{asset_file.name}")
                media_type = (
                    "video" if asset_file.suffix.lower() in {".mp4", ".mov", ".m4v", ".webm", ".avi"}
                    else "image"
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
                    with destination.open("xb") as target:
                        copied_paths.append(destination)
                        with asset_file.open("rb") as source:
                            shutil.copyfileobj(source, target)
                    shutil.copystat(asset_file, destination)
                    image_names.append(copied_name)
                verified_assets.append((str(asset_file), media_type, str(expected_hash), copied_name))

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
                "status": "待验证", "tags": result.get("tags") if isinstance(result.get("tags"), list) else [],
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
                        "path": str(Path(row["prompt_path"]).resolve()),
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
                (asset_id, row["prompt_path"], "text", "source_prompt", row["prompt_sha256"], now),
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
                     WHERE asset_id=? ORDER BY created_at DESC LIMIT 50
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
                rows = snapshot.execute(
                    "SELECT kind,payload_json FROM assets ORDER BY updated_at DESC, rowid DESC"
                ).fetchall()
                snapshot.close()
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
                "format_version": 1,
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
                        or manifest.get("format_version") != 1
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
                check.close()
            except sqlite3.DatabaseError as exc:
                raise StorageError("备份数据库无法打开或已损坏") from exc
            if integrity != "ok" or not {"assets", "metadata"}.issubset(tables):
                raise StorageError("备份数据库完整性或结构检查未通过")

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
                rows = verify_db.execute(
                    "SELECT kind,payload_json FROM assets ORDER BY updated_at DESC, rowid DESC"
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

