"""Stage 2 schema and explicit migration for isolated database copies."""
import hashlib
import json
import uuid
import tempfile
from pathlib import Path

from migration_preflight import inspect_database
from storage import StorageError

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
    """CREATE TABLE garments (
        id TEXT PRIMARY KEY, sku TEXT NOT NULL DEFAULT '', name TEXT NOT NULL,
        category TEXT NOT NULL, subcategory TEXT NOT NULL DEFAULT '',
        color TEXT NOT NULL DEFAULT '', silhouette TEXT NOT NULL DEFAULT '',
        fabric TEXT NOT NULL DEFAULT '', details TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
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
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(case_id, version))""",
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


def migrate(store):
    """Migrate an isolated store after verified backup; never deletes old records."""
    temp_root = Path(tempfile.gettempdir()).resolve()
    if not store.home.resolve().is_relative_to(temp_root):
        raise StorageError("当前实验迁移仅允许操作临时测试目录")
    with store.exclusive_operation():
        before = inspect_database(store.db_path, store.home / "references")
        if before["schema_version"] not in {"1", "2"} or (before["integrity"] != ["ok"] or before["quick_check"] != ["ok"]):
            raise StorageError("数据库版本或完整性检查失败")
        if before["schema_errors"] or before["foreign_key_errors"] or any(
            item["status"] != "verified" for item in before["attachments"]
        ):
            raise StorageError("附件或外键检查失败，迁移已中止")
        if before["schema_version"] == "2":
            return {"status": "already_migrated", "before": before}
        backup = store.create_full_backup(store.home / "references", app_version="pre-stage2")
        with store._session() as con:
            con.execute("BEGIN IMMEDIATE")
            for statement in SCHEMA:
                con.execute(statement)
            rows = con.execute(
                "SELECT id, payload_json FROM assets WHERE kind='case' ORDER BY id").fetchall()
            for row in rows:
                payload = json.loads(row["payload_json"])
                if isinstance(payload.get("body"), str):
                    body = payload["body"]
                    source_id = str(uuid.uuid4())
                    con.execute(
                        "INSERT INTO prompt_sources(id,case_id,version,body,sha256) VALUES(?,?,?,?,?)",
                        (source_id, row["id"], 1, body, hashlib.sha256(body.encode("utf-8")).hexdigest()))
            con.execute("UPDATE metadata SET value='2' WHERE key='schema_version'")
            if con.execute("PRAGMA foreign_key_check").fetchall():
                raise StorageError("迁移后外键检查失败")
        return {"status": "migrated", "backup": str(backup), "before": before}
