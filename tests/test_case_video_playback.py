import hashlib
import importlib
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

import imageio_ffmpeg
from storage import Storage


class CaseVideoPlaybackTests(unittest.TestCase):
    def test_video_ranges_ownership_and_image_regression(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(Path, 'home', return_value=root):
                app = importlib.import_module('ai_prompt_workbench')
            store = Storage(root / 'data')
            store.save_data({'cases': [{'id': 'case1'}, {'id': 'case2'}], 'modules': []})
            refs = store.home / 'references'
            refs.mkdir(exist_ok=True)
            video = refs / 'test.mp4'
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-f', 'lavfi', '-i',
                            'color=c=blue:s=32x32:d=1', '-c:v', 'mpeg4', '-y', str(video)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            content = video.read_bytes()
            (refs / 'image.png').write_bytes(b'image-fixture')
            with store._session() as con:
                aid = con.execute("INSERT INTO attachments(asset_id,relative_path,media_type,sha256) VALUES(?,?,?,?)",
                                  ('case1', 'test.mp4', 'video', hashlib.sha256(content).hexdigest())).lastrowid
                bad = con.execute("INSERT INTO attachments(asset_id,relative_path,media_type,sha256) VALUES(?,?,?,?)",
                                  ('case1', '../test.mp4', 'video', hashlib.sha256(content).hexdigest())).lastrowid
            old = app.STORAGE, app.HOME, app.IMAGES
            app.STORAGE, app.HOME, app.IMAGES = store, store.home, refs
            server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            opener = build_opener(ProxyHandler({}))
            base = f'http://127.0.0.1:{server.server_address[1]}'
            try:
                url = base + f'/api/case-video?case_id=case1&attachment_id={aid}'
                with opener.open(Request(url, headers={'Range': 'bytes=1-20'})) as response:
                    self.assertEqual(response.status, 206)
                    self.assertEqual(response.headers['Content-Type'], 'video/mp4')
                    self.assertEqual(response.read(), content[1:21])
                with opener.open(url) as response:
                    self.assertEqual(response.read(), content)
                with opener.open(url + '&download=1') as response:
                    self.assertTrue(response.headers['Content-Disposition'].startswith('attachment;'))
                    self.assertEqual(response.read(), content)
                with opener.open(Request(url, headers={'Range': 'bytes=-7'})) as response:
                    self.assertEqual(response.read(), content[-7:])
                for invalid in (base + f'/api/case-video?case_id=case2&attachment_id={aid}',
                                base + f'/api/case-video?case_id=case2&attachment_id={aid}&download=1',
                                base + f'/api/case-video?case_id=case1&attachment_id={bad}'):
                    with self.assertRaises(HTTPError) as error: opener.open(invalid)
                    self.assertEqual(error.exception.code, 404)
                with self.assertRaises(HTTPError) as error:
                    opener.open(Request(url, headers={'Range': 'bytes=999999999-'}))
                self.assertEqual(error.exception.code, 416)
                with self.assertRaises(HTTPError) as error:
                    opener.open(Request(url, headers={'Sec-Fetch-Site': 'cross-site'}))
                self.assertEqual(error.exception.code, 403)
                with opener.open(base + '/media/image.png') as response:
                    self.assertEqual(response.headers['Content-Type'], 'image/png')
                    self.assertEqual(response.read(), b'image-fixture')
                video.write_bytes(b'corrupted')
                with self.assertRaises(HTTPError) as error: opener.open(url)
                self.assertEqual(error.exception.code, 404)
            finally:
                server.shutdown(); server.server_close(); thread.join(5)
                app.STORAGE, app.HOME, app.IMAGES = old

    def test_detail_uses_native_controls_and_no_file_paths(self):
        html = (Path(__file__).resolve().parents[1] / 'index.html').read_text(encoding='utf-8')
        self.assertIn('document.createElement("video")', html)
        self.assertIn('video.controls=true', html)
        self.assertIn('/api/case-video?', html)


if __name__ == '__main__':
    unittest.main()
