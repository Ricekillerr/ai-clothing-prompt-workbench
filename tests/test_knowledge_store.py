import sqlite3
import tempfile
import unittest
from pathlib import Path

from knowledge_store import KnowledgeStore
from stage2_schema import migrate
from storage import Storage, StorageError


class KnowledgeStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Storage(Path(self.tmp.name))
        self.store.save_data({"cases": [
            {"id": "c1", "body": "原文"}, {"id": "c2", "body": "第二案例"}
        ], "modules": []})
        migrate(self.store)
        self.knowledge = KnowledgeStore(self.store)

    def test_many_to_many_status_and_duplicate_candidates(self):
        one = self.knowledge.create_garment("马甲", "上装", "A1")
        two = self.knowledge.create_garment("另一件马甲", "上装", "a1")
        link1 = self.knowledge.link_case("c1", one)
        self.assertEqual(link1, self.knowledge.link_case("c1", one))
        link2 = self.knowledge.link_case("c2", one)
        self.assertNotEqual(link1, link2)
        self.assertNotEqual(link1, self.knowledge.link_case("c1", one, "反穿"))
        self.knowledge.set_case_status(link1, "success", "approved")
        self.knowledge.set_case_status(link2, "failed", "pending")
        with self.store._session() as con:
            self.assertEqual(con.execute("SELECT status FROM case_garments WHERE id=?", (link1,)).fetchone()[0], "success")
            self.assertEqual(con.execute("SELECT status FROM case_garments WHERE id=?", (link2,)).fetchone()[0], "failed")
            self.assertEqual(con.execute("SELECT count(*) FROM garment_candidates WHERE review_status='pending'").fetchone()[0], 1)
        self.assertNotEqual(one, two)

    def test_prompt_trace_and_case_boundaries(self):
        garment = self.knowledge.create_garment("马甲", "上装")
        link = self.knowledge.link_case("c1", garment)
        source = self.knowledge.append_source("c1", "😀黑色马甲")
        segment = self.knowledge.add_segment(source, "overall_appearance", 1, 3, link)
        with self.store._session() as con:
            self.assertEqual(con.execute("SELECT original_text FROM prompt_segments WHERE id=?", (segment,)).fetchone()[0], "黑色")
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("UPDATE prompt_sources SET body='changed' WHERE id=?", (source,))
        other = self.knowledge.append_source("c2", "其他")
        with self.assertRaises(StorageError):
            self.knowledge.add_segment(other, "fabric", 0, 1, link)
        with self.assertRaises(StorageError):
            self.knowledge.add_ps_note("c2", "口袋", "多余装饰", link)
        note = self.knowledge.add_ps_note("c1", "口袋", "多余装饰", link)
        with self.store._session() as con:
            self.assertEqual(con.execute("SELECT status FROM ps_notes WHERE id=?", (note,)).fetchone()[0], "pending")


if __name__ == "__main__":
    unittest.main()
