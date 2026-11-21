"""Experimental generation-result schema. Not connected to app migration.

Only operates on an already-open, in-memory test connection. Production
activation requires a versioned migration and backup/restore integration.
"""
import sqlite3


def install_test_schema(con: sqlite3.Connection) -> None:
    """Create relational constraints only in an isolated in-memory database."""
    database = con.execute("PRAGMA database_list").fetchone()
    if not database or database[2]:
        raise ValueError("experimental schema requires an in-memory database")
    if con.in_transaction:
        raise ValueError("test schema setup requires a clean connection")
    con.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE generation_results (
          id TEXT PRIMARY KEY,
          case_id TEXT NOT NULL REFERENCES assets(id),
          video_attachment_id INTEGER NOT NULL REFERENCES attachments(id),
          prompt_source_id TEXT NOT NULL REFERENCES prompt_sources(id),
          prompt_snapshot TEXT NOT NULL,
          prompt_english TEXT NOT NULL DEFAULT '',
          model TEXT NOT NULL DEFAULT '', parameters TEXT NOT NULL DEFAULT '',
          notes TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL CHECK(status IN ('success','failed','pending')),
          deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1)),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(case_id,video_attachment_id,prompt_source_id)
        );
        CREATE TABLE generation_reference_images (
          result_id TEXT NOT NULL REFERENCES generation_results(id),
          attachment_id INTEGER NOT NULL REFERENCES attachments(id),
          role TEXT NOT NULL DEFAULT '', priority TEXT NOT NULL DEFAULT '',
          PRIMARY KEY(result_id,attachment_id)
        );
        CREATE TABLE generation_garments (
          result_id TEXT NOT NULL REFERENCES generation_results(id),
          case_garment_id TEXT NOT NULL REFERENCES case_garments(id),
          PRIMARY KEY(result_id,case_garment_id)
        );
        CREATE TRIGGER generation_result_insert_validate BEFORE INSERT ON generation_results
        WHEN NOT EXISTS (SELECT 1 FROM assets c JOIN attachments a ON a.asset_id=c.id
          JOIN prompt_sources p ON p.case_id=c.id WHERE c.id=NEW.case_id AND c.kind='case'
          AND a.id=NEW.video_attachment_id AND a.media_type='video'
          AND p.id=NEW.prompt_source_id AND p.body=NEW.prompt_snapshot)
        BEGIN SELECT RAISE(ABORT,'generation result must match case and prompt'); END;
        CREATE TRIGGER generation_result_snapshot_immutable BEFORE UPDATE OF
          case_id,video_attachment_id,prompt_source_id,prompt_snapshot,prompt_english ON generation_results
        BEGIN SELECT RAISE(ABORT,'generation provenance is immutable'); END;
        CREATE TRIGGER generation_image_validate BEFORE INSERT ON generation_reference_images
        WHEN NOT EXISTS (SELECT 1 FROM generation_results r JOIN attachments a ON a.asset_id=r.case_id
          WHERE r.id=NEW.result_id AND a.id=NEW.attachment_id AND a.media_type='image')
        BEGIN SELECT RAISE(ABORT,'reference image belongs to another case'); END;
        CREATE TRIGGER generation_garment_validate BEFORE INSERT ON generation_garments
        WHEN NOT EXISTS (SELECT 1 FROM generation_results r JOIN case_garments g ON g.case_id=r.case_id
          WHERE r.id=NEW.result_id AND g.id=NEW.case_garment_id)
        BEGIN SELECT RAISE(ABORT,'garment belongs to another case'); END;
        CREATE TRIGGER generation_image_identity_immutable BEFORE UPDATE OF result_id,attachment_id ON generation_reference_images
        BEGIN SELECT RAISE(ABORT,'reference identity is immutable'); END;
        CREATE TRIGGER generation_garment_identity_immutable BEFORE UPDATE OF result_id,case_garment_id ON generation_garments
        BEGIN SELECT RAISE(ABORT,'garment identity is immutable'); END;
        CREATE TRIGGER generation_attachment_immutable BEFORE UPDATE OF asset_id,media_type ON attachments
        WHEN EXISTS (SELECT 1 FROM generation_results WHERE video_attachment_id=OLD.id)
          OR EXISTS (SELECT 1 FROM generation_reference_images WHERE attachment_id=OLD.id)
        BEGIN SELECT RAISE(ABORT,'generation attachment ownership is immutable'); END;
        CREATE TRIGGER generation_case_garment_immutable BEFORE UPDATE OF case_id ON case_garments
        WHEN EXISTS (SELECT 1 FROM generation_garments WHERE case_garment_id=OLD.id)
        BEGIN SELECT RAISE(ABORT,'generation garment ownership is immutable'); END;
    """)
