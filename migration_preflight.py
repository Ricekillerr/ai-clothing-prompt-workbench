"""Read-only preflight for the stage 2 migration."""
import hashlib
import json
import sqlite3
from pathlib import Path


def inspect_database(database, references):
    database = Path(database).resolve(strict=True)
    references = Path(references)
    uri = database.as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    try:
        integrity = [row[0] for row in con.execute("PRAGMA quick_check")]
        foreign_keys = [tuple(row) for row in con.execute("PRAGMA foreign_key_check")]
        tables = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        required = {"metadata", "assets", "attachments"}
        if not required.issubset(tables):
            raise ValueError("数据库缺少必要的现有表")
        version_row = con.execute(
            "SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        counts = {name: con.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
                  for name in sorted(tables)}
        prompt_hashes = []
        for row in con.execute("SELECT id, payload_json FROM assets ORDER BY id"):
            payload = json.loads(row["payload_json"])
            body = payload.get("body")
            if isinstance(body, str):
                prompt_hashes.append({"asset_id": row["id"],
                                      "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()})
        attachments = []
        root = references.resolve()
        for row in con.execute("SELECT id, relative_path, sha256 FROM attachments ORDER BY id"):
            path = Path(row["relative_path"])
            if path.is_absolute() or ".." in path.parts:
                status = "unsafe_path"
            else:
                target = references / path
                resolved = target.resolve()
                if not resolved.is_relative_to(root):
                    status = "unsafe_path"
                elif not target.is_file():
                    status = "missing"
                else:
                    digest = hashlib.sha256(target.read_bytes()).hexdigest()
                    status = "verified" if row["sha256"] and digest == row["sha256"].lower() else (
                        "hash_mismatch" if row["sha256"] else "unverified")
            attachments.append({"id": row["id"], "status": status})
        return {"schema_version": version_row[0] if version_row else None,
                "integrity": integrity, "foreign_key_errors": foreign_keys,
                "counts": counts, "prompt_hashes": prompt_hashes, "attachments": attachments}
    finally:
        con.close()
