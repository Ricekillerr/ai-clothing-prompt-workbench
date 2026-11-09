# -*- coding: utf-8 -*-
"""Prompt Atelier V0.5.0 local-first storage layer.

SQLite stores structured records. Original JSON is retained as a migration
source and is never overwritten by the migration process.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


class StorageError(RuntimeError):
    pass


class Storage:
    def __init__(self, home: Path, legacy_json: Path | None = None):
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

    def _initialize(self) -> None:
        existed = self.db_path.exists()
        try:
            with self._connect() as con:
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

        if not existed and self.legacy_json.exists():
            self.import_legacy_json(self.legacy_json)

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
        for key in ("cases", "modules"):
            if key not in data or not isinstance(data[key], list):
                raise StorageError(f"旧 JSON 缺少有效的 {key} 数组；原文件保持不变。")
            if any(not isinstance(item, dict) for item in data[key]):
                raise StorageError(f"旧 JSON 的 {key} 中存在非对象记录；原文件保持不变。")
        return data

    def import_legacy_json(self, path: Path | None = None) -> dict[str, int]:
        source = Path(path) if path else self.legacy_json
        data = self._read_legacy(source)
        counts = {"cases": 0, "modules": 0}
        try:
            with self._connect() as con:
                # Import is all-or-nothing. Existing IDs are not overwritten.
                for key, kind in (("cases", "case"), ("modules", "module")):
                    for item in data[key]:
                        asset_id = str(item.get("id") or "").strip()
                        if not asset_id:
                            raise StorageError(f"发现没有 id 的{key}记录，已取消整批迁移。")
                        cur = con.execute(
                            "INSERT OR IGNORE INTO assets(id,kind,payload_json,created_at,updated_at) "
                            "VALUES(?,?,?,?,?)",
                            (asset_id, kind, json.dumps(item, ensure_ascii=False),
                             item.get("created_at"), item.get("updated_at")),
                        )
                        counts[key] += cur.rowcount
                con.execute(
                    "INSERT OR REPLACE INTO metadata(key,value) VALUES('legacy_import_source',?)",
                    (str(source),),
                )
        except Exception as exc:
            if isinstance(exc, StorageError):
                raise
            raise StorageError(f"JSON 迁移失败，事务已回滚：{exc}") from exc
        return counts

    def load_data(self) -> dict[str, Any]:
        try:
            with self._connect() as con:
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
        for key in ("cases", "modules"):
            if any(not isinstance(item, dict) or not item.get("id") for item in data[key]):
                raise StorageError(f"{key} 中存在无效记录，数据库未修改。")
        try:
            with self._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                incoming_ids = {
                    str(item["id"])
                    for key in ("cases", "modules")
                    for item in data[key]
                }
                # Upsert existing assets first so attachment rows remain linked.
                for key, kind in (("cases", "case"), ("modules", "module")):
                    for item in data[key]:
                        con.execute(
                            """INSERT INTO assets(id,kind,payload_json,created_at,updated_at)
                               VALUES(?,?,?,?,?)
                               ON CONFLICT(id) DO UPDATE SET
                                 kind=excluded.kind,
                                 payload_json=excluded.payload_json,
                                 created_at=excluded.created_at,
                                 updated_at=excluded.updated_at""",
                            (str(item["id"]), kind, json.dumps(item, ensure_ascii=False),
                             item.get("created_at"), item.get("updated_at")),
                        )
                existing_ids = {
                    row[0] for row in con.execute("SELECT id FROM assets").fetchall()
                }
                removed_ids = existing_ids - incoming_ids
                if removed_ids:
                    con.executemany("DELETE FROM assets WHERE id=?", [(x,) for x in removed_ids])
        except Exception as exc:
            raise StorageError(f"保存失败，事务已回滚：{exc}") from exc

    def backup(self, destination: Path | None = None) -> Path:
        target = Path(destination) if destination else self.home / "backups" / "prompt_library.sqlite3"
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="db_backup_", suffix=".sqlite3", dir=target.parent)
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            with self._connect() as source, sqlite3.connect(temp_path) as dest:
                source.backup(dest)
            # Validate backup before atomically publishing it.
            with sqlite3.connect(temp_path) as check:
                result = check.execute("PRAGMA quick_check").fetchone()
                if not result or result[0] != "ok":
                    raise StorageError("备份完整性检查失败。")
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
