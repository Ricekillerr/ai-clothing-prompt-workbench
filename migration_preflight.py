"""Read-only inventory and integrity checks before schema migration."""
import hashlib
import json
import sqlite3
import warnings
from pathlib import Path, PureWindowsPath

from storage import _attachment_status

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
        schema_errors = []
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
        for row in con.execute("SELECT id, payload_json FROM assets ORDER BY id"):
            payload = json.loads(row["payload_json"])
            body = payload.get("body")
            if isinstance(body, str):
                prompt_hashes.append({"asset_id": row["id"],
                                      "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()})
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
        for row in con.execute(
            "SELECT id, relative_path, media_type, sha256 FROM attachments ORDER BY id"
        ):
            status = _attachment_status(references, row["relative_path"], row["media_type"], row["sha256"])
            attachments.append({"id": row["id"], "status": status})
        return {"schema_version": version, "schema_errors": schema_errors,
                "quick_check": quick, "integrity": integrity,
                "foreign_key_errors": foreign_keys, "counts": counts, "kind_counts": kind_counts,
                "prompt_hashes": prompt_hashes, "revision_prompt_hashes": revision_prompt_hashes,
                "attachments": attachments}
    finally:
        con.close()
