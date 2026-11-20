"""Exercise the staged candidate in a fresh profile and restricted PATH."""
import json
import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

source_candidate = Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory(prefix="candidate_") as tmp:
    candidate = Path(tmp) / "程序 中文 空格"
    shutil.copytree(source_candidate, candidate)
    python = candidate / "runtime" / "python.exe"
    launcher = candidate / "PromptAtelier.exe"
    profile = Path(tmp) / "中文 空格" / "profile"
    profile.mkdir(parents=True)
    env = dict(os.environ, USERPROFILE=str(profile), HOME=str(profile),
               PATH=str(Path(os.environ["SystemRoot"]) / "System32"),
               PROMPT_ATELIER_NO_BROWSER="1", PYTHONNOUSERSITE="1",
               HTTP_PROXY="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9",
               NO_PROXY="localhost,127.0.0.1")
    for variable in ("PYTHONHOME", "PYTHONPATH", "IMAGEIO_FFMPEG_EXE"):
        env.pop(variable, None)
    probe = r'''
import hashlib, json, subprocess, sys
from pathlib import Path
root=Path(sys.argv[1]).resolve(); profile=Path(sys.argv[2]); sys.path.insert(0,str(root))
import PIL, imageio_ffmpeg
from PIL import Image
from storage import Storage, KnowledgeStore, create_protected_copy
from collector import scan_folder
assert Path(sys.executable).resolve().is_relative_to(root / 'runtime')
assert Path(PIL.__file__).resolve().is_relative_to(root / 'runtime')
assert Path(imageio_ffmpeg.__file__).resolve().is_relative_to(root / 'runtime')
ffmpeg=Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
assert ffmpeg.is_relative_to(root / 'runtime') and ffmpeg.is_file()
image=profile/'actual.png'; Image.new('RGB',(8,8),'red').save(image)
with Image.open(image) as im: im.load(); assert im.size==(8,8)
legacy=Storage(profile/'AI_Prompt_Asset_Workbench')
legacy.save_data({'cases':[{'id':'c1','body':'中文提示词'}], 'modules':[]})
create_protected_copy(legacy,profile/'AI_Prompt_Asset_Workbench_v2')
store=Storage(profile/'AI_Prompt_Asset_Workbench_v2'/'data')
with store._session() as con:
    con.execute("INSERT INTO garments(id,name,category) VALUES('g1','马甲','上装')")
    con.execute("INSERT INTO case_garments(id,case_id,garment_id) VALUES('cg1','c1','g1')")
    con.execute("INSERT INTO prompt_sources(id,case_id,version,body,sha256) VALUES('v2','c1',2,'新版','hash')")
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
    data_home = profile / "AI_Prompt_Asset_Workbench_v2" / "data"
    config = data_home / "isolated-settings.json"
    config.write_text('{"private":"keep"}', encoding="utf-8")
    video = next((data_home / "references").glob("video_*.mp4"))
    video_hash = hashlib.sha256(video.read_bytes()).hexdigest()
    def counts():
        with sqlite3.connect(data_home / "prompt_library.sqlite3") as con:
            return tuple(con.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                         for table in ("assets", "garments", "case_garments", "prompt_sources", "attachments"))
    before = counts()
    stage = Path(tmp) / "isolated-update"
    stage.mkdir()
    shutil.copy2(candidate / "index.html", stage / "index.html")
    digest = hashlib.sha256((stage / "index.html").read_bytes()).hexdigest()
    (stage / "update_meta.json").write_text(json.dumps({"version": "0.6.5", "files": {"index.html": digest}}), encoding="utf-8")
    subprocess.run([str(python), str(candidate / "updater.py"), str(stage), str(candidate),
                    str(candidate / "启动AI服装提示词工作台.bat")],
                   env=env, cwd=candidate, check=True, timeout=120)
    assert counts() == before and hashlib.sha256(video.read_bytes()).hexdigest() == video_hash
    assert config.read_text(encoding="utf-8") == '{"private":"keep"}'
    req = Request("http://127.0.0.1:8765/api/client/close", data=b"{}",
                  headers={"Content-Type": "application/json"}, method="POST")
    with opener.open(req, timeout=3) as response:
        assert json.load(response)["ok"]
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        try:
            with opener.open("http://127.0.0.1:8765/api/version", timeout=1):
                pass
        except OSError:
            break
        time.sleep(0.2)
    else:
        raise RuntimeError("updated server did not stop")
    print("bundled updater preserved V2 database, video and settings")
