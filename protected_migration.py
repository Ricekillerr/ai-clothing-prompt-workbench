"""Isolated V2 copy experiment. No production migration entry point."""
import os
import tempfile
from pathlib import Path

from migration_preflight import inspect_database
from stage2_schema import migrate
from storage import Storage, StorageError


def create_protected_copy(source, destination):
    """Copy an isolated V1 store into a separate V2 directory without replacing V1."""
    destination = Path(destination)
    temp_root = Path(tempfile.gettempdir()).resolve()
    if not source.home.resolve().is_relative_to(temp_root):
        raise StorageError("隔离迁移实验仅支持临时目录")
    if destination.exists() or destination.parent.resolve() != source.home.resolve().parent:
        raise StorageError("目标必须是与来源同级且不存在的目录")
    with source.exclusive_operation():
        report = inspect_database(source.db_path, source.home / "references")
        if (report["schema_version"] != "1" or report["schema_errors"]
                or report["quick_check"] != ["ok"] or report["integrity"] != ["ok"]
                or report["foreign_key_errors"]
                or any(item["status"] != "verified" for item in report["attachments"])):
            raise StorageError("来源预检失败")
        backup = source.create_full_backup(source.home / "references", app_version="pre-stage2")
        work = Path(tempfile.mkdtemp(prefix=".stage2-copy-", dir=destination.parent))
        target = Storage(work)
        target.restore_full_backup(backup)
        migration = migrate(target)
        after = inspect_database(target.db_path, work / "references")
        if (after["schema_version"] != "2" or after["schema_errors"]
                or after["quick_check"] != ["ok"] or after["integrity"] != ["ok"]
                or after["foreign_key_errors"] or after["counts"]["assets"] != report["counts"]["assets"]
                or after["prompt_hashes"] != report["prompt_hashes"]
                or after["attachments"] != report["attachments"]):
            raise StorageError(f"隔离复制校验失败，副本保留在 {work}")
        if destination.exists():
            raise StorageError("目标目录已出现，副本已保留")
        os.rename(work, destination)
        migration["backup"] = str(destination / Path(migration["backup"]).relative_to(work))
        return {"status": "created", "source_backup": str(backup),
                "destination": str(destination), "migration": migration, "report": after}
