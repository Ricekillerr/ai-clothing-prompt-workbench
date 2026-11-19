# -*- coding: utf-8 -*-
"""Prompt Atelier updater: verified install, backup, rollback and reliable Windows restart."""
import hashlib
import importlib.util
import webbrowser
from urllib.request import build_opener, ProxyHandler
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import tempfile
import traceback
from pathlib import Path
from storage import active_data_home

LOG_MAX_BYTES = 1024 * 1024
LOG_BACKUP_COUNT = 3

ALLOWED = {
    "ai_prompt_workbench.py", "index.html", "启动AI服装提示词工作台.bat",
    "updater.py", "使用说明_AI服装提示词资产工作台_V0.4.3.txt",
    "使用说明_Prompt_Atelier_V1.0_候选版.txt",
    "storage.py", "collector.py", "case_matcher.py", "ai_analyzer.py"
}

def write_log(root, message):
    try:
        root = Path(root)
        log = root / "_update_error.log"
        safe_message = str(message)
        for sensitive_path, label in (
            (root, "<install-root>"),
            (Path.home(), "<user-home>"),
        ):
            token = str(sensitive_path)
            if token:
                safe_message = re.sub(
                    re.escape(token), label, safe_message, flags=re.IGNORECASE
                )
        safe_message = re.sub(
            r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+",
            r"\1[REDACTED]",
            safe_message,
        )
        safe_message = re.sub(
            r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;]+",
            r"\1[REDACTED]",
            safe_message,
        )
        safe_message = re.sub(r"\bsk-[A-Za-z0-9_-]{16,}\b", "[REDACTED]", safe_message)
        line = time.strftime("[%Y-%m-%d %H:%M:%S] ") + safe_message[-32768:] + "\n"
        encoded_size = len(line.encode("utf-8"))
        if log.exists() and log.stat().st_size + encoded_size > LOG_MAX_BYTES:
            for index in range(LOG_BACKUP_COUNT, 0, -1):
                source = log if index == 1 else Path(str(log) + f".{index - 1}")
                target = Path(str(log) + f".{index}")
                if target.exists():
                    target.unlink()
                if source.exists():
                    os.replace(source, target)
        with log.open("a", encoding="utf-8") as stream:
            stream.write(line)
    except Exception:
        pass

def service_running():
    s = socket.socket()
    s.settimeout(0.25)
    try:
        return s.connect_ex(("127.0.0.1", 8765)) == 0
    finally:
        s.close()

def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def replace_with_retry(src, dst):
    last = None
    for _ in range(20):
        try:
            os.replace(src, dst)
            return
        except OSError as e:
            last = e
            time.sleep(0.35)
    raise last

HEALTH_TIMEOUT = 45

class UnsafeToRestore(RuntimeError):
    """A process may still own live data. Preserve the journal and stop."""

def record_state(root, state, phase):
    state["phase"] = phase
    journal = root / "_update_transaction.json"
    temp = journal.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as output:
        json.dump(state, output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temp, journal)

def wait_previous_process(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:  # process already exited
                return
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if kernel.WaitForSingleObject(handle, 30000) != 0:
                raise TimeoutError("旧工作台进程尚未退出")
        finally:
            kernel.CloseHandle(handle)
    else:
        for _ in range(120):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.25)
        raise TimeoutError("旧工作台进程尚未退出")

def launch_workbench(root, launcher, version):
    """Own the exact process being probed so a failed launch can be stopped safely."""
    env = dict(os.environ, PROMPT_ATELIER_NO_BROWSER="1", PROMPT_ATELIER_UPDATE_CHILD="1")
    # Prevent same-size, same-second replacements from loading stale bytecode.
    env["PYTHONPYCACHEPREFIX"] = str(root / "_update_bytecode_unused")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    diagnostic = tempfile.TemporaryFile()
    try:
        process = subprocess.Popen([sys.executable, str(root / "ai_prompt_workbench.py")],
                                   cwd=root, env=env, close_fds=True, stderr=diagnostic)
    except BaseException:
        diagnostic.close()
        raise
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + HEALTH_TIMEOUT
    last_error = None
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"工作台启动或数据库初始化失败，退出码 {process.returncode}")
            try:
                def read(path):
                    with opener.open("http://127.0.0.1:8765" + path, timeout=2) as response:
                        return response.read()
                if json.loads(read("/api/version"))["version"] != version:
                    raise ValueError("工作台版本不符合预期")
                data = json.loads(read("/api/data"))
                if not all(isinstance(data.get(k), list) for k in ("cases", "modules")):
                    raise ValueError("数据库读取结果不完整")
                if not read("/index.html").strip():
                    raise ValueError("前端文件为空")
                if process.poll() is None:
                    return
            except (OSError, ValueError, KeyError) as exc:
                last_error = exc
            time.sleep(0.25)
        raise TimeoutError("工作台健康检查超时") from last_error
    except BaseException:
        if process.poll() is None:
            try:
                process.kill()
                process.wait(timeout=10)
            except OSError as exc:
                if process.poll() is None:
                    raise UnsafeToRestore("无法确认候选进程已停止") from exc
            except subprocess.TimeoutExpired as exc:
                raise UnsafeToRestore("等待候选进程停止超时") from exc
        diagnostic.seek(0, os.SEEK_END)
        diagnostic.seek(max(0, diagnostic.tell() - 32768))
        message = diagnostic.read().decode("utf-8", errors="replace")
        if message:
            write_log(root, message)
        raise
    finally:
        diagnostic.close()

def old_install(root):
    entry = root / "ai_prompt_workbench.py"
    if not entry.is_file():
        return None, None, None
    match = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)', entry.read_text(encoding="utf-8-sig"), re.M)
    if not match:
        raise ValueError("无法识别旧版本")
    spec = importlib.util.spec_from_file_location("previous_storage", root / "storage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return match[1], module.Storage, getattr(module, "SCHEMA_VERSION", 1)

def restore_snapshot(storage_type, home, snapshot):
    # Validate into a separate store even if the failed migration made the live DB unreadable.
    recovered = Path(tempfile.mkdtemp(prefix=".update_recovery_", dir=home))
    store = storage_type(recovered)
    store.restore_full_backup(snapshot)
    retained = recovered / "failed_version"
    retained.mkdir()
    for name in ("prompt_library.sqlite3-wal", "prompt_library.sqlite3-shm", "prompt_library.sqlite3-journal",
                 "prompt_library.sqlite3", "prompt_library.json", "references"):
        live = home / name
        if live.exists():
            os.replace(live, retained / name)
        source = recovered / name
        if source.exists():
            os.replace(source, live)

def reopen_browser():
    if os.environ.get("PROMPT_ATELIER_NO_BROWSER") != "1":
        try:
            webbrowser.open("http://127.0.0.1:8765")
        except Exception:
            pass

def main():
    if len(sys.argv) < 4:
        return 2

    stage, root, launcher = map(Path, sys.argv[1:4])
    root = root.resolve()
    launcher = launcher.resolve()
    meta_file = stage / "update_meta.json"

    try:
        if not meta_file.is_file():
            raise FileNotFoundError("更新暂存目录缺少 update_meta.json")

        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        expected = meta.get("files")
        if not isinstance(expected, dict) or not expected:
            raise ValueError("更新清单 files 为空或格式不正确")
        if any(name not in ALLOWED for name in expected):
            raise ValueError("更新清单包含不允许替换的文件")

        for name, digest in expected.items():
            p = stage / name
            if not p.is_file() or sha256(p) != digest:
                raise ValueError(f"更新文件缺失或 SHA-256 校验失败：{name}")

        if len(sys.argv) > 4:
            wait_previous_process(int(sys.argv[4]))

        # Wait for the old local server to release the application files.
        for _ in range(120):
            if not service_running():
                break
            time.sleep(0.25)
        else:
            raise TimeoutError("等待工作台关闭超时，未执行文件替换")

        backup = Path(tempfile.mkdtemp(prefix="prompt_atelier_backup_", dir=str(root)))
        transaction = []
        version_dst = root / "installed_version.txt"
        version_backup = backup / "installed_version.txt"
        version_existed = version_dst.is_file()
        if version_existed:
            shutil.copy2(version_dst, version_backup)

        journal = root / "_update_transaction.json"
        old_version, storage_type, supported_schema = old_install(root)
        data_home = active_data_home(Path.home())
        if (data_home != Path.home() / "AI_Prompt_Asset_Workbench"
                and (supported_schema is None or supported_schema < 2)):
            raise UnsafeToRestore("旧程序不支持 V2 数据，已停止更新并保留当前数据")
        snapshot = None
        if storage_type:
            # The old server has released its port. Capture writes since the HTTP-side snapshot.
            snapshot = storage_type(data_home).create_full_backup(data_home / "references", app_version=old_version)
        state = {"old_version": old_version, "new_version": str(meta["version"]), "backup": str(backup),
                 "data_backup": str(snapshot) if snapshot else None, "files": list(expected),
                 "previous_files": [n for n in expected if (root / n).is_file()],
                 "had_version_marker": version_existed}
        with journal.open("x", encoding="utf-8") as stream:
            json.dump(dict(state, phase="preparing"), stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        attempted_start = False
        try:
            for name in expected:
                dst = root / name
                old_exists = dst.is_file()
                backup_path = backup / name
                if old_exists:
                    backup_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(dst, backup_path)
                transaction.append((dst, backup_path, old_exists))

            record_state(root, state, "installing")
            for name in expected:
                src = stage / name
                dst = root / name
                tmp = root / (name + ".update_tmp")
                if name.lower().endswith((".bat", ".cmd")):
                    # GitHub serves text files with LF; Windows cmd.exe batch parsing
                    # requires CRLF for reliable labels and percent-variable handling.
                    payload = src.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
                    payload = payload.replace(b"\n", b"\r\n")
                    tmp.write_bytes(payload)
                    shutil.copystat(src, tmp)
                else:
                    shutil.copy2(src, tmp)
                replace_with_retry(tmp, dst)

            version_tmp = root / "installed_version.txt.update_tmp"
            version_tmp.write_text(str(meta["version"]), encoding="utf-8")
            replace_with_retry(version_tmp, version_dst)

            record_state(root, state, "checking_health")
            attempted_start = True
            launch_workbench(root, launcher, str(meta["version"]))

        except UnsafeToRestore:
            record_state(root, state, "recovery_required")
            raise
        except Exception as update_error:
            write_log(root, traceback.format_exc())
            record_state(root, state, "rolling_back")
            if service_running():
                raise UnsafeToRestore("端口仍在使用，已保留备份并暂停恢复") from update_error
            rollback_errors = []
            for dst, backup_path, existed in reversed(transaction):
                try:
                    if existed and backup_path.is_file():
                        restore = dst.with_name(dst.name + ".restore_tmp")
                        shutil.copy2(backup_path, restore)
                        replace_with_retry(restore, dst)
                    elif dst.exists():
                        dst.unlink()
                except Exception as rollback_error:
                    rollback_errors.append(f"{dst.name}: {type(rollback_error).__name__}: {rollback_error}")
            try:
                if version_existed and version_backup.is_file():
                    restore = version_dst.with_name(version_dst.name + ".restore_tmp")
                    shutil.copy2(version_backup, restore)
                    replace_with_retry(restore, version_dst)
                elif version_dst.exists():
                    version_dst.unlink()
            except Exception as rollback_error:
                rollback_errors.append(f"{version_dst.name}: {type(rollback_error).__name__}: {rollback_error}")
            for name in expected:
                for suffix in (".update_tmp", ".restore_tmp"):
                    leftover = root / (name + suffix)
                    try:
                        if leftover.exists():
                            leftover.unlink()
                    except OSError as cleanup_error:
                        rollback_errors.append(f"{leftover.name} cleanup: {cleanup_error}")
            version_tmp = root / "installed_version.txt.update_tmp"
            try:
                if version_tmp.exists():
                    version_tmp.unlink()
            except OSError as cleanup_error:
                rollback_errors.append(f"{version_tmp.name} cleanup: {cleanup_error}")
            if rollback_errors:
                details = "; ".join(rollback_errors)[:2000]
                raise RuntimeError(
                    "更新失败且自动回滚未完整完成；原始文件备份保留在 "
                    f"{backup}。请勿删除该备份目录。回滚失败项：{details}"
                ) from update_error
            if attempted_start and snapshot:
                restore_snapshot(storage_type, data_home, snapshot)
            if old_version:
                record_state(root, state, "checking_restored_version")
                launch_workbench(root, launcher, old_version)
            record_state(root, state, "rolled_back")
            journal.unlink()
            if old_version:
                reopen_browser()
            raise

        finally:
            shutil.rmtree(stage, ignore_errors=True)

        record_state(root, state, "committed")
        journal.unlink()
        keep = root / "_update_backup"
        if keep.exists():
            shutil.rmtree(keep, ignore_errors=True)
        try:
            backup.rename(keep)
        except Exception as e:
            write_log(root, f"更新已完成，但整理备份目录失败：{e}")

        reopen_browser()
        return 0

    except Exception:
        write_log(root, "更新失败：\n" + traceback.format_exc())
        return 9

if __name__ == "__main__":
    sys.exit(main())