# -*- coding: utf-8 -*-
"""Prompt Atelier updater: verified install, backup, rollback and reliable Windows restart."""
import hashlib
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

def launch_workbench(root, launcher):
    # Windows' os.startfile delegates .bat execution to the registered command
    # processor and avoids the fragile `cmd /c start /D ...` argument parsing.
    if not launcher.is_file():
        raise FileNotFoundError(f"启动脚本不存在：{launcher}")
    if os.name == "nt":
        os.startfile(str(launcher))
    else:
        subprocess.Popen([sys.executable, str(root / "ai_prompt_workbench.py")],
                         cwd=str(root), close_fds=True)

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

        try:
            for name in expected:
                dst = root / name
                old_exists = dst.is_file()
                backup_path = backup / name
                if old_exists:
                    backup_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(dst, backup_path)
                transaction.append((dst, backup_path, old_exists))

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

        except Exception as update_error:
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
            raise

        finally:
            shutil.rmtree(stage, ignore_errors=True)

        keep = root / "_update_backup"
        if keep.exists():
            shutil.rmtree(keep, ignore_errors=True)
        try:
            backup.rename(keep)
        except Exception as e:
            write_log(root, f"更新已完成，但整理备份目录失败：{e}")

        try:
            launch_workbench(root, launcher)
        except Exception:
            write_log(root, "更新文件已安装，但自动重启失败：\n" + traceback.format_exc())
            return 10

        return 0

    except Exception:
        write_log(root, "更新失败：\n" + traceback.format_exc())
        return 9

if __name__ == "__main__":
    sys.exit(main())