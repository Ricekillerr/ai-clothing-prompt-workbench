import importlib
import json
import hashlib
import os
from io import BytesIO
from PIL import Image
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError

from stage2_schema import migrate
from storage import Storage


class KnowledgeHTTPTests(unittest.TestCase):
    def test_v2_knowledge_http_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Storage(Path(tmp))
            store.save_data({"cases": [{"id": "case1", "body": "原文"}], "modules": []})
            migrate(store)
            app = importlib.import_module("ai_prompt_workbench")
            previous = app.STORAGE
            app.STORAGE = store
            server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 5)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            self.addCleanup(setattr, app, "STORAGE", previous)
            opener = build_opener(ProxyHandler({}))
            base = "http://127.0.0.1:" + str(server.server_address[1])
            def post(payload):
                req = Request(base + "/api/knowledge", data=json.dumps(payload).encode(),
                              headers={"Content-Type": "application/json"}, method="POST")
                with opener.open(req, timeout=3) as response:
                    return json.loads(response.read())
            created = post({"action": "create_garment", "name": "马甲", "category": "上装"})
            garment = created["result"]
            linked = post({"action": "link_case", "case_id": "case1", "garment_id": garment})
            post({"action": "set_status", "id": linked["result"], "status": "success",
                  "review_status": "approved"})
            with opener.open(base + "/api/knowledge?action=garment&id=" + garment, timeout=3) as response:
                detail = json.loads(response.read())["data"]
            self.assertEqual(detail["garment"]["name"], "马甲")
            self.assertEqual(detail["cases"][0]["status"], "success")
            note = post({"action": "add_ps_note", "case_id": "case1", "area": "口袋", "problem": "多余装饰"})
            with opener.open(base + "/api/knowledge?action=notes&id=case1", timeout=3) as response:
                notes = json.loads(response.read())["data"]
            self.assertEqual(notes[0]["id"], note["result"])
            old_home, old_images = app.HOME, app.IMAGES
            app.HOME, app.IMAGES = Path(tmp), Path(tmp) / "references"
            app.IMAGES.mkdir(exist_ok=True)
            output = BytesIO()
            Image.frombytes("RGB", (512, 512), os.urandom(512 * 512 * 3)).save(output, format="PNG")
            image = output.getvalue()
            (app.IMAGES / "one.png").write_bytes(image)
            with store._session() as con:
                attachment_id = con.execute(
                    "INSERT INTO attachments(asset_id,relative_path,sha256) VALUES('case1','one.png',?)",
                    (hashlib.sha256(image).hexdigest(),),
                ).lastrowid
            post({"action": "add_ps_asset", "note_id": note["result"],
                  "attachment_id": attachment_id, "role": "before"})
            self.addCleanup(setattr, app, "HOME", old_home)
            self.addCleanup(setattr, app, "IMAGES", old_images)
            with opener.open(base + "/api/backup/full-json", timeout=10) as response:
                backup = response.read()
            self.assertGreater(len(backup), 256 * 1024)
            envelope = json.loads(backup)
            self.assertEqual(envelope["format"], "PromptAtelierFullBackupJSON")
            post({"action": "create_garment", "name": "临时外套", "category": "外套"})
            request = Request(base + "/api/backup/full-json/restore", data=backup,
                              headers={"Content-Type": "application/json"}, method="POST")
            with opener.open(request, timeout=10) as response:
                restored = json.loads(response.read())
            self.assertTrue(restored["ok"])
            with store._session() as con:
                self.assertEqual(con.execute("SELECT count(*) FROM garments").fetchone()[0], 1)
                self.assertEqual(con.execute("SELECT count(*) FROM ps_notes").fetchone()[0], 1)
            self.assertTrue((Path(tmp) / "backups" / restored["safety_backup"]).is_file())
            self.assertEqual((app.IMAGES / "one.png").read_bytes(), image)
            with store._session() as con:
                self.assertEqual(con.execute(
                    "SELECT count(*) FROM ps_note_assets").fetchone()[0], 1)
            damaged = bytearray(backup)
            damaged[len(damaged) // 2] = ord("!")
            request = Request(base + "/api/backup/full-json/restore", data=bytes(damaged),
                              headers={"Content-Type": "application/json"}, method="POST")
            with self.assertRaises(HTTPError):
                opener.open(request, timeout=10)
            self.assertEqual((app.IMAGES / "one.png").read_bytes(), image)
            with store._session() as con:
                self.assertEqual(con.execute("SELECT count(*) FROM garments").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
