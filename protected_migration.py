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
        # Reserve a previously absent container, preventing a competing process
        # from replacing any existing directory. A missing marker means recovery
        # must be reviewed before the application uses this location.
        destination.mkdir()
        data_dir = destination / "data"
        os.rename(work, data_dir)
        marker = destination / "stage2-ready.json"
        with marker.open("x", encoding="utf-8") as stream:
            stream.write('{"schema_version":2}')
            stream.flush()
            os.fsync(stream.fileno())
        migration["backup"] = str(data_dir / Path(migration["backup"]).relative_to(work))
        return {"status": "created", "source_backup": str(backup),
                "destination": str(data_dir), "migration": migration, "report": after}


def recover_protected_copy(destination, staged_copy):
    """Finish an interrupted test activation without overwriting any directory."""
    destination = Path(destination)
    staged_copy = Path(staged_copy)
    temp_root = Path(tempfile.gettempdir()).resolve()
    if not destination.resolve().is_relative_to(temp_root):
        raise StorageError("隔离恢复实验仅支持临时目录")
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
    report = inspect_database(candidate / "prompt_library.sqlite3", candidate / "references")
    if (report["schema_version"] != "2" or report["schema_errors"]
            or report["quick_check"] != ["ok"] or report["integrity"] != ["ok"]
            or report["foreign_key_errors"]
            or any(item["status"] != "verified" for item in report["attachments"])):
        raise StorageError("隔离副本未通过恢复校验")
    if candidate == staged_copy:
        os.rename(staged_copy, data)
    with (destination / "stage2-ready.json").open("x", encoding="utf-8") as stream:
        stream.write('{"schema_version":2}')
        stream.flush()
        os.fsync(stream.fileno())
    return {"status": "recovered", "destination": str(data)}
