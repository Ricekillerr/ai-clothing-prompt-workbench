# -*- coding: utf-8 -*-
"""独立更新助手：等待本地服务退出，备份并替换程序文件，再重启。"""
import sys, time, shutil, subprocess, socket
from pathlib import Path

ALLOWED = {
    "ai_prompt_workbench.py", "index.html",
    "启动AI服装提示词工作台.bat", "updater.py",
    "使用说明_AI服装提示词资产工作台_V0.4.1.txt"
}
def running():
    s=socket.socket(); s.settimeout(.2)
    try: return s.connect_ex(("127.0.0.1",8765)) == 0
    finally: s.close()

def main():
    if len(sys.argv) < 4: return
    stage, root, launcher = map(Path, sys.argv[1:4])
    backup = root / "_update_backup"
    backup.mkdir(exist_ok=True)
    for _ in range(120):
        if not running(): break
        time.sleep(.25)
    else: return
    changed=[]
    try:
        for src in stage.iterdir():
            if not src.is_file() or src.name not in ALLOWED: continue
            dst=root/src.name
            if dst.exists(): shutil.copy2(dst, backup/(src.name+".bak"))
            tmp=dst.with_suffix(dst.suffix+".new")
            shutil.copy2(src,tmp); tmp.replace(dst); changed.append(dst)
        version=stage/"version.txt"
        if version.exists(): (root/"installed_version.txt").write_text(version.read_text(encoding="utf-8"),encoding="utf-8")
    except Exception:
        for dst in reversed(changed):
            old=backup/(dst.name+".bak")
            if old.exists(): shutil.copy2(old,dst)
        return
    finally:
        shutil.rmtree(stage,ignore_errors=True)
    if launcher.exists():
        subprocess.Popen(["cmd","/c","start","","/D",str(root),str(launcher)],cwd=str(root),
                         stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",512))
if __name__=="__main__": main()
