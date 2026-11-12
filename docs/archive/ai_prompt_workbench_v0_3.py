# -*- coding: utf-8 -*-
import json, mimetypes, base64, uuid, webbrowser, threading
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent
HOME = Path.home() / "AI_Prompt_Asset_Workbench"
IMAGES = HOME / "references"
DATA = HOME / "prompt_library.json"
HOME.mkdir(exist_ok=True); IMAGES.mkdir(exist_ok=True)

def load_data():
    if DATA.exists():
        try:
            d=json.loads(DATA.read_text(encoding="utf-8"))
            d.setdefault("cases",[]); d.setdefault("modules",[])
            return d
        except Exception: pass
    # Optional first-run import if a V0.2 JSON backup is placed beside this launcher.
    for p in [ROOT/"prompt_library.json", ROOT/"V0.2_prompt_library.json"]:
        if p.exists():
            try:
                d=json.loads(p.read_text(encoding="utf-8")); d.setdefault("cases",[]); d.setdefault("modules",[])
                save_data(d); return d
            except Exception: pass
    return {"version":2,"cases":[],"modules":[]}

def save_data(d):
    tmp=DATA.with_suffix(".tmp")
    tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding="utf-8")
    tmp.replace(DATA)

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def send(self,code,body,ctype):
        self.send_response(code); self.send_header("Content-Type",ctype); self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        p=unquote(self.path.split("?",1)[0])
        if p in ("/","/index.html"):
            f=ROOT/"index.html"; self.send(200,f.read_bytes(),"text/html; charset=utf-8")
        elif p=="/api/data":
            self.send(200,json.dumps(load_data(),ensure_ascii=False).encode("utf-8"),"application/json; charset=utf-8")
        elif p.startswith("/media/"):
            f=IMAGES/Path(p[len("/media/"):]).name
            if f.exists(): self.send(200,f.read_bytes(),mimetypes.guess_type(str(f))[0] or "application/octet-stream")
            else:self.send(404,b"not found","text/plain")
        else:self.send(404,b"not found","text/plain")
    def do_POST(self):
        n=int(self.headers.get("Content-Length","0")); raw=self.rfile.read(n)
        try:
            d=json.loads(raw.decode("utf-8"))
            if self.path=="/api/data":
                if not isinstance(d,dict): raise ValueError("bad data")
                d.setdefault("cases",[]); d.setdefault("modules",[]); save_data(d)
                self.send(200,b'{"ok":true}',"application/json")
            elif self.path=="/api/image":
                ext=Path(d.get("name","image.png")).suffix.lower()
                if ext not in [".png",".jpg",".jpeg",".webp",".gif",".bmp"]: ext=".png"
                content=base64.b64decode(d.get("data","").split(",",1)[-1])
                if len(content)>15*1024*1024: raise ValueError("图片不能超过15MB")
                name=uuid.uuid4().hex[:16]+ext; (IMAGES/name).write_bytes(content)
                self.send(200,json.dumps({"path":name}).encode(),"application/json")
            else:self.send(404,b'{}',"application/json")
        except Exception as e:self.send(400,json.dumps({"error":str(e)}).encode("utf-8"),"application/json")

def main():
    server=ThreadingHTTPServer(("127.0.0.1",8765),Handler)
    url="http://127.0.0.1:8765"
    threading.Timer(.7,lambda:webbrowser.open(url)).start()
    print("AI服装提示词资产工作台 V0.3 已启动："+url)
    print("本地数据目录："+str(HOME))
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
if __name__=="__main__": main()
