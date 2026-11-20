"""Exercise the staged candidate in a fresh profile and restricted PATH."""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

candidate = Path(sys.argv[1]).resolve()
python = candidate / "runtime" / "python.exe"
launcher = candidate / "PromptAtelier.exe"
with tempfile.TemporaryDirectory(prefix="candidate_") as tmp:
    profile = Path(tmp) / "中文 空格" / "profile"
    profile.mkdir(parents=True)
    env = dict(os.environ, USERPROFILE=str(profile), HOME=str(profile),
               PATH=str(Path(os.environ["SystemRoot"]) / "System32"),
               PROMPT_ATELIER_NO_BROWSER="1", PYTHONNOUSERSITE="1")
    for variable in ("PYTHONHOME", "PYTHONPATH", "IMAGEIO_FFMPEG_EXE"):
        env.pop(variable, None)
    probe = r'''
import hashlib, json, subprocess, sys
from pathlib import Path
root=Path(sys.argv[1]); profile=Path(sys.argv[2]); sys.path.insert(0,str(root))
import PIL, imageio_ffmpeg
from PIL import Image
from storage import Storage, KnowledgeStore, create_protected_copy
from collector import scan_folder
assert Path(sys.executable).resolve().is_relative_to(root / 'runtime')
ffmpeg=Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
assert ffmpeg.is_relative_to(root / 'runtime') and ffmpeg.is_file()
image=profile/'actual.png'; Image.new('RGB',(8,8),'red').save(image)
with Image.open(image) as im: im.load(); assert im.size==(8,8)
legacy=Storage(profile/'AI_Prompt_Asset_Workbench')
legacy.save_data({'cases':[{'id':'c1','body':'中文提示词'}], 'modules':[]})
create_protected_copy(legacy,profile/'AI_Prompt_Asset_Workbench_v2')
store=Storage(profile/'AI_Prompt_Asset_Workbench_v2'/'data')
inbox=profile/'视频 来源'; inbox.mkdir(); video=inbox/'原始 视频.mp4'
subprocess.run([str(ffmpeg),'-v','error','-f','lavfi','-i','color=c=blue:s=32x32:d=1',
                '-c:v','mpeg4','-y',str(video)],check=True)
digest=hashlib.sha256(video.read_bytes()).hexdigest()
source=store.register_scan_source(str(inbox)); store.save_scan_results(source,scan_folder(inbox))
knowledge=KnowledgeStore(store)
first=knowledge.import_video('c1',video,digest); second=knowledge.import_video('c1',video,digest)
assert first==second and hashlib.sha256((store.home/'references'/first['path']).read_bytes()).hexdigest()==digest
with store._session() as con: assert con.execute("SELECT COUNT(*) FROM attachments WHERE asset_id='c1'").fetchone()[0]==1
print(json.dumps({'pillow':PIL.__version__,'ffmpeg':imageio_ffmpeg.get_ffmpeg_version(),'video':'decoded and imported'}))
'''
    subprocess.run([str(python), "-I", "-c", probe, str(candidate), str(profile)],
                   env=env, cwd=candidate, check=True, timeout=180)
    process = subprocess.Popen([str(launcher)], cwd=candidate, env=env)
    opener = build_opener(ProxyHandler({}))
    try:
        deadline = time.monotonic() + 40
        while True:
            if process.poll() is not None:
                raise RuntimeError(f"candidate launcher exited: {process.returncode}")
            try:
                with opener.open("http://127.0.0.1:8765/api/version", timeout=2) as response:
                    result = json.load(response)
                if result.get("schema_version") == "2":
                    break
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("candidate server did not start")
            time.sleep(0.2)
        req = Request("http://127.0.0.1:8765/api/client/close", data=b"{}",
                      headers={"Content-Type": "application/json"}, method="POST")
        with opener.open(req, timeout=3) as response:
            assert json.load(response)["ok"]
        process.wait(timeout=40)
        if process.returncode:
            raise RuntimeError(f"candidate shutdown failed: {process.returncode}")
        print("standalone EXE HTTP startup and shutdown passed")
    finally:
        if process.poll() is None:
            subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"),
                            "/PID", str(process.pid), "/T", "/F"], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            process.wait(timeout=15)
