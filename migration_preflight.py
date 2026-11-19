"""Read-only inventory and integrity checks before schema migration."""
import hashlib
import json
import sqlite3
import warnings
from pathlib import Path, PureWindowsPath

MAX_IMAGE_BYTES = 100 * 1024 * 1024
MAX_IMAGE_PIXELS = 80_000_000
FORMATS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP",
           ".gif": "GIF", ".bmp": "BMP"}


def _link(path):
    try:
        return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())
    except OSError:
        return True


def _attachment_status(root, relative_path, media_type, expected):
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
        if media_type != "image" or image_format is None:
            return "unsupported"
        initial = current.stat()
        if initial.st_size > MAX_IMAGE_BYTES:
            return "corrupt"
        digest = hashlib.sha256()
        with current.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if expected and digest.hexdigest() != str(expected).lower():
            return "hash_mismatch"
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
        return "verified" if expected else "unverified"
    except PermissionError:
        return "unverified"
    except OSError:
        return "unverified"


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
            required_v2 = {"garments", "garment_revisions", "case_garments", "prompt_sources", "prompt_segments",
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
