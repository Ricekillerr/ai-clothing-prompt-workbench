# -*- coding: utf-8 -*-
"""V0.5.0 updater."""
import hashlib,json,os,shutil,socket,subprocess,sys,time,tempfile
from pathlib import Path
ALLOWED={"ai_prompt_workbench.py","index.html","启动AI服装提示词工作台.bat","updater.py"}
def running():
 s=socket.socket();s.settimeout(.25)
 try:return s.connect_ex(("127.0.0.1",8765))==0
 finally:s.close()
def digest(p):
 h=hashlib.sha256()
 with p.open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):h.update(b)
 return h.hexdigest()
def replace(src,dst):
 err=None
 for _ in range(30):
  try:os.replace(src,dst);return
  except OSError as e:err=e;time.sleep(.35)
 raise err
def main():
 if len(sys.argv)<4:return 2
 stage,root,launcher=map(Path,sys.argv[1:4]);mf=stage/"update_meta.json"
 if not mf.is_file():return 3
 try:
  files=json.loads(mf.read_text(encoding="utf-8")).get("files")
  if not isinstance(files,dict) or not files or any(n not in ALLOWED for n in files):return 4
  for n,h in files.items():
   if not (stage/n).is_file() or digest(stage/n)!=h:return 5
  for _ in range(120):
   if not running():break
   time.sleep(.25)
  else:return 6
  backup=Path(tempfile.mkdtemp(prefix="prompt_atelier_program_backup_",dir=str(root)));tx=[]
  try:
   for n in files:
    dst=root/n;old=dst.is_file();bp=backup/n
    if old:shutil.copy2(dst,bp)
    tx.append((dst,bp,old))
   for n in files:
    tmp=root/(n+".update_tmp");shutil.copy2(stage/n,tmp);replace(tmp,root/n)
  except Exception:
   for dst,bp,old in reversed(tx):
    try:
     if old and bp.is_file():
      tmp=dst.with_name(dst.name+".restore_tmp");shutil.copy2(bp,tmp);replace(tmp,dst)
     elif dst.exists():dst.unlink()
    except Exception:pass
   return 7
  finally:shutil.rmtree(stage,ignore_errors=True)
  keep=root/"_program_update_backup"
  if keep.exists():shutil.rmtree(keep,ignore_errors=True)
  try:backup.rename(keep)
  except Exception:pass
  if launcher.is_file():subprocess.Popen(["cmd","/c","start","", "/D",str(root),str(launcher)],cwd=str(root),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",512))
  return 0
 except Exception:return 8
if __name__=="__main__":sys.exit(main())
