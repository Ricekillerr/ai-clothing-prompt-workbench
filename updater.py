# -*- coding: utf-8 -*-
"""V0.4.2 独立更新助手：完整性复核、逐文件备份、失败回滚、重启。"""
import hashlib, json, os, shutil, socket, subprocess, sys, time, tempfile
from pathlib import Path
ALLOWED = {"ai_prompt_workbench.py", "storage.py", "collector.py", "case_matcher.py", "ai_analyzer.py", "index.html", "启动AI服装提示词工作台.bat", "updater.py", "使用说明_AI服装提示词资产工作台_V0.4.2.txt"}
def service_running():
    s = socket.socket(); s.settimeout(.25)
    try: return s.connect_ex(("127.0.0.1", 8765)) == 0
    finally: s.close()
def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""): h.update(chunk)
    return h.hexdigest()
def replace_with_retry(src, dst):
    last = None
    for _ in range(20):
        try: os.replace(src, dst); return
        except OSError as e: last = e; time.sleep(.35)
    raise last
def main():
    if len(sys.argv) < 4: return 2
    stage, root, launcher = map(Path, sys.argv[1:4])
    meta_file = stage / "update_meta.json"
    if not meta_file.is_file(): return 3
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8")); expected = meta.get("files")
        if not isinstance(expected, dict) or not expected: return 4
        if any(name not in ALLOWED for name in expected): return 5
        for name, digest in expected.items():
            p = stage / name
            if not p.is_file() or sha256(p) != digest: return 6
        for _ in range(120):
            if not service_running(): break
            time.sleep(.25)
        else: return 7
        backup = Path(tempfile.mkdtemp(prefix="prompt_atelier_backup_", dir=str(root)))
        transaction = []
        version_dst = root / "installed_version.txt"; version_backup = backup / "installed_version.txt"
        version_existed = version_dst.is_file()
        if version_existed: shutil.copy2(version_dst, version_backup)
        try:
            for name in expected:
                dst = root / name; old_exists = dst.is_file(); backup_path = backup / name
                if old_exists:
                    backup_path.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(dst, backup_path)
                transaction.append((dst, backup_path, old_exists))
            for name in expected:
                src = stage / name; dst = root / name; tmp = root / (name + ".update_tmp")
                shutil.copy2(src, tmp); replace_with_retry(tmp, dst)
            version_tmp = root / "installed_version.txt.update_tmp"
            version_tmp.write_text(str(meta["version"]), encoding="utf-8")
            replace_with_retry(version_tmp, version_dst)
        except Exception:
            for dst, backup_path, existed in reversed(transaction):
                try:
                    if existed and backup_path.is_file():
                        restore = dst.with_name(dst.name + ".restore_tmp")
                        shutil.copy2(backup_path, restore); replace_with_retry(restore, dst)
                    elif dst.exists(): dst.unlink()
                except Exception: pass
            try:
                if version_existed and version_backup.is_file():
                    restore = version_dst.with_name(version_dst.name + ".restore_tmp")
                    shutil.copy2(version_backup, restore); replace_with_retry(restore, version_dst)
                elif version_dst.exists(): version_dst.unlink()
            except Exception: pass
            return 8
        finally: shutil.rmtree(stage, ignore_errors=True)
        keep = root / "_update_backup"
        if keep.exists(): shutil.rmtree(keep, ignore_errors=True)
        try: backup.rename(keep)
        except Exception: pass
        if launcher.is_file():
            subprocess.Popen(["cmd", "/c", "start", "", "/D", str(root), str(launcher)], cwd=str(root),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 512))
        return 0
    except Exception: return 9
if __name__ == "__main__": sys.exit(main())
