import sqlite3
import tempfile
import unittest
from pathlib import Path

from generation_result_schema import install_test_schema


class GenerationSchemaTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.executescript("""
          CREATE TABLE assets(id TEXT PRIMARY KEY,kind TEXT);
          CREATE TABLE attachments(id INTEGER PRIMARY KEY,asset_id TEXT,media_type TEXT);
          CREATE TABLE prompt_sources(id TEXT PRIMARY KEY,case_id TEXT,body TEXT);
          CREATE TABLE case_garments(id TEXT PRIMARY KEY,case_id TEXT);
          INSERT INTO assets VALUES('c1','case'),('c2','case');
          INSERT INTO attachments VALUES(1,'c1','video'),(2,'c1','image'),(3,'c2','image'),(4,'c1','video');
          INSERT INTO prompt_sources VALUES('p1','c1','original'),('p2','c1','revised'),('p3','c2','other');
          INSERT INTO case_garments VALUES('g1','c1'),('g2','c2');
        """)
        install_test_schema(self.db)

    def tearDown(self):
        self.db.close()

    def add(self, ident, video, prompt, text):
        self.db.execute('INSERT INTO generation_results(id,case_id,video_attachment_id,prompt_source_id,prompt_snapshot,status) VALUES(?,?,?,?,?,?)',
                        (ident, 'c1', video, prompt, text, 'success'))

    def test_versions_and_multiple_videos(self):
        self.add('r1', 1, 'p1', 'original')
        self.add('r2', 4, 'p2', 'revised')
        self.assertEqual(self.db.execute('SELECT prompt_snapshot FROM generation_results ORDER BY id').fetchall(), [('original',), ('revised',)])
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE generation_results SET prompt_snapshot='changed' WHERE id='r1'")

    def test_reject_cross_case_and_mismatched_snapshot(self):
        for prompt, text in [('p3', 'other'), ('p1', 'wrong')]:
            with self.subTest(prompt=prompt), self.assertRaises(sqlite3.IntegrityError):
                self.add('bad', 1, prompt, text)

    def test_image_and_garment_ownership(self):
        self.add('r1', 1, 'p1', 'original')
        self.db.execute("INSERT INTO generation_reference_images VALUES('r1',2,'front','primary')")
        self.db.execute("INSERT INTO generation_garments VALUES('r1','g1')")
        for sql in ("INSERT INTO generation_reference_images VALUES('r1',3,'front','primary')",
                    "INSERT INTO generation_garments VALUES('r1','g2')",
                    "UPDATE attachments SET asset_id='c2' WHERE id=2",
                    "UPDATE case_garments SET case_id='c2' WHERE id='g1'"):
            with self.assertRaises(sqlite3.IntegrityError): self.db.execute(sql)

    def test_duplicate_and_soft_delete_restore(self):
        self.add('r1', 1, 'p1', 'original')
        with self.assertRaises(sqlite3.IntegrityError): self.add('r2', 1, 'p1', 'original')
        self.db.execute("UPDATE generation_results SET deleted=1 WHERE id='r1'")
        self.db.execute("UPDATE generation_results SET deleted=0 WHERE id='r1'")
        self.assertEqual(self.db.execute("SELECT deleted FROM generation_results WHERE id='r1'").fetchone()[0], 0)

    def test_real_database_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            connection = sqlite3.connect(Path(folder) / 'test.db')
            try:
                with self.assertRaises(ValueError): install_test_schema(connection)
            finally:
                connection.close()

    def test_transaction_rollback_and_sqlite_copy(self):
        self.db.commit()
        self.db.execute('BEGIN')
        self.add('transient', 1, 'p1', 'original')
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT count(*) FROM generation_results').fetchone()[0], 0)
        self.add('saved', 1, 'p1', 'original')
        self.db.execute("INSERT INTO generation_reference_images VALUES('saved',2,'front','primary')")
        self.db.commit()
        copy = sqlite3.connect(':memory:')
        try:
            self.db.backup(copy)
            self.assertEqual(copy.execute('SELECT prompt_snapshot FROM generation_results').fetchone()[0], 'original')
            self.assertEqual(copy.execute('SELECT attachment_id FROM generation_reference_images').fetchone()[0], 2)
        finally:
            copy.close()


if __name__ == '__main__':
    unittest.main()
