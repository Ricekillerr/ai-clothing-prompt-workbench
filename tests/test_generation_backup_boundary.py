"""Exercise existing backups and the relationship prototype in isolated stores."""
import hashlib
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

import imageio_ffmpeg
from PIL import Image

from generation_result_schema import install_test_schema
from storage import DataService, KnowledgeStore, Storage, StorageError, migrate


class GenerationBackupBoundaryTests(unittest.TestCase):
    def test_v1_and_v2_backup_restore_with_real_media_and_memory_prototype(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            v1 = Storage(root / 'source')
            v1.save_data({'cases': [{'id': 'c1', 'body': '原始提示词'}], 'modules': []})
            (v1.home / 'references').mkdir(exist_ok=True)
            v1_backup = v1.create_full_backup(v1.home / 'references')
            isolated = Storage(root / 'legacy-restore')
            isolated.restore_full_backup(v1_backup)
            self.assertEqual(isolated.load_data()['cases'][0]['body'], '原始提示词')

            migrate(v1)
            refs = v1.home / 'references'
            image = refs / 'ref.png'
            Image.new('RGB', (8, 8), 'blue').save(image)
            video = refs / 'result.mp4'
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-f', 'lavfi', '-i',
                            'color=c=blue:s=32x32:d=1', '-c:v', 'mpeg4', '-y', str(video)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
            with v1._session() as db:
                image_id = db.execute("INSERT INTO attachments(asset_id,relative_path,media_type,sha256) VALUES(?,?,?,?)",
                                      ('c1', image.name, 'image', digest(image))).lastrowid
                video_id = db.execute("INSERT INTO attachments(asset_id,relative_path,media_type,sha256) VALUES(?,?,?,?)",
                                      ('c1', video.name, 'video', digest(video))).lastrowid
            knowledge = KnowledgeStore(v1)
            garment = knowledge.create_garment('外套', '外套', 'SKU1')
            link = knowledge.link_case('c1', garment)
            source = knowledge.list_sources('c1')[0]
            archive = v1.create_full_backup(refs)
            self.assertTrue(DataService(v1).verify_backup(archive)['ok'])

            restored = Storage(root / 'v2-restore')
            migrate(restored)
            restored.restore_full_backup(archive)
            restored.restore_full_backup(archive)
            self.assertEqual(digest(restored.home / 'references' / video.name), digest(video))
            self.assertEqual(digest(restored.home / 'references' / image.name), digest(image))
            with restored._session() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM case_garments WHERE id=?', (link,)).fetchone()[0], 1)
                memory = sqlite3.connect(':memory:')
                try:
                    db.backup(memory)
                    install_test_schema(memory)
                    memory.execute("INSERT INTO generation_results(id,case_id,video_attachment_id,prompt_source_id,prompt_snapshot,status) VALUES(?,?,?,?,?,?)",
                                   ('r1', 'c1', video_id, source['id'], source['body'], 'success'))
                    memory.execute("INSERT INTO generation_reference_images VALUES(?,?,?,?)", ('r1', image_id, 'front', 'primary'))
                    memory.execute("INSERT INTO generation_garments VALUES(?,?)", ('r1', link))
                    self.assertEqual(memory.execute('PRAGMA foreign_key_check').fetchall(), [])
                finally:
                    memory.close()
            with self.assertRaises(StorageError):
                restored.restore_full_backup(v1_backup)
            self.assertEqual(digest(restored.home / 'references' / video.name), digest(video))


if __name__ == '__main__':
    unittest.main()
