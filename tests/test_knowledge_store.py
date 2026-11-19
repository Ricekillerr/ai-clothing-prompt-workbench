import sqlite3
import hashlib
from io import BytesIO
from PIL import Image
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

    def test_legacy_json_import_is_additive_and_idempotent(self):
        payload = {"cases": [
            {"id": "c1", "body": "禁止覆盖"},
            {"id": "c3", "body": "全新原文", "custom": {"old": "field"},
             "outfit_items": [{"name": "马甲", "category": "上装"}]},
            {"name": "缺少 ID"},
        ], "modules": [{"id": "m1", "body": "面料"}]}
        report = self.knowledge.import_legacy_json(payload)
        self.assertEqual((report["inserted"], report["existing"], len(report["errors"])), (2, 1, 1))
        again = self.knowledge.import_legacy_json(payload)
        self.assertEqual((again["inserted"], again["existing"], len(again["errors"])), (0, 3, 1))
        cases = {row["id"]: row for row in self.store.load_data()["cases"]}
        self.assertEqual(cases["c1"]["body"], "原文")
        self.assertEqual(cases["c3"]["custom"], {"old": "field"})
        self.assertEqual(self.knowledge.list_sources("c3")[0]["body"], "全新原文")
        with self.store._session() as con:
            self.assertEqual(con.execute(
                "SELECT count(*) FROM case_garments WHERE case_id='c3'").fetchone()[0], 1)
            self.assertEqual(con.execute(
                "SELECT count(*) FROM migration_review WHERE case_id='c3'").fetchone()[0], 1)

    def test_existing_case_editor_creates_immutable_prompt_version(self):
        self.store.save_data({"cases": [
            {"id": "c1", "body": "编辑后的原文"}, {"id": "c2", "body": "第二案例"}
        ], "modules": []})
        versions = self.knowledge.list_sources("c1")
        self.assertEqual([entry["body"] for entry in versions], ["编辑后的原文", "原文"])
        self.store.save_data({"cases": [
            {"id": "c1", "body": "编辑后的原文"}, {"id": "c2", "body": "第二案例"}
        ], "modules": []})
        self.assertEqual(len(self.knowledge.list_sources("c1")), 2)

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

    def test_edit_garment_retains_revision_and_case_links(self):
        garment = self.knowledge.create_garment("马甲", "上装")
        self.knowledge.link_case("c1", garment)
        self.knowledge.update_garment(garment, {"name": "羽绒马甲"})
        detail = self.knowledge.garment_detail(garment)
        self.assertEqual(detail["garment"]["name"], "羽绒马甲")
        self.assertEqual(detail["cases"][0]["case_id"], "c1")
        with self.store._session() as con:
            revision = con.execute(
                "SELECT payload_json FROM garment_revisions WHERE garment_id=?", (garment,)
            ).fetchone()
        self.assertIn("马甲", revision[0])
        with self.assertRaises(StorageError):
            self.knowledge.update_garment(garment, {"name": ""})
        self.assertEqual(self.knowledge.garment_detail(garment)["garment"]["name"], "羽绒马甲")

    def test_delete_restore_keeps_v2_knowledge_and_backup(self):
        garment = self.knowledge.create_garment("马甲", "上装")
        link = self.knowledge.link_case("c1", garment)
        self.knowledge.set_case_status(link, "success", "approved")
        source = self.knowledge.append_source("c1", "😀黑色马甲")
        segment = self.knowledge.add_segment(source, "overall_appearance", 1, 3, link)
        note = self.knowledge.add_ps_note("c1", "口袋", "多余装饰", link)
        references = self.store.home / "references"
        references.mkdir()
        output = BytesIO()
        Image.new("RGB", (2, 2), "red").save(output, format="PNG")
        image = output.getvalue()
        (references / "one.png").write_bytes(image)
        with self.store._session() as con:
            attachment = con.execute(
                "INSERT INTO attachments(asset_id,relative_path,role,sha256) VALUES('c1','one.png','reference',?)",
                (hashlib.sha256(image).hexdigest(),),
            ).lastrowid
        self.knowledge.add_ps_asset(note, attachment, "before")
        self.store.save_data({"cases": [{"id": "c2", "body": "第二案例"}], "modules": []})
        self.assertEqual([case["id"] for case in self.store.load_data()["cases"]], ["c2"])
        deleted = self.store.list_deleted_assets()
        self.assertEqual(deleted[0]["asset_id"], "c1")
        backup = self.store.create_full_backup(self.store.home / "references")
        self.store.restore_full_backup(backup)
        self.store.restore_asset_revision("c1", deleted[0]["revision_id"], expected_absent=True)
        with self.store._session() as con:
            self.assertEqual(con.execute("SELECT status FROM case_garments WHERE id=?", (link,)).fetchone()[0], "success")
            self.assertEqual(con.execute("SELECT original_text FROM prompt_segments WHERE id=?", (segment,)).fetchone()[0], "黑色")
            self.assertEqual(con.execute("SELECT attachment_id FROM ps_note_assets WHERE note_id=?", (note,)).fetchone()[0], attachment)
        self.assertIn("c1", [item["id"] for item in self.store.load_data()["cases"]])
        self.assertEqual((references / "one.png").read_bytes(), image)

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
        self.assertEqual(self.knowledge.list_ps_notes("c1")[0]["id"], note)
        self.assertEqual(self.knowledge.list_sources("c1")[0]["id"], source)
        with self.store._session() as con:
            self.assertEqual(con.execute("SELECT status FROM ps_notes WHERE id=?", (note,)).fetchone()[0], "pending")

    def test_references_and_ps_images_must_belong_to_same_case(self):
        with self.store._session() as con:
            one = con.execute(
                "INSERT INTO attachments(asset_id,relative_path,role) VALUES('c1','one.png','reference')"
            ).lastrowid
            other = con.execute(
                "INSERT INTO attachments(asset_id,relative_path,role) VALUES('c2','two.mp4','reference')"
            ).lastrowid
        source = self.knowledge.append_source("c1", "请参照 @图片1")
        segment = self.knowledge.add_segment(source, "model_constraints", 0, 2)
        self.knowledge.add_segment_reference(segment, one, "@图片1")
        with self.assertRaises(StorageError):
            self.knowledge.add_segment_reference(segment, other, "@图片1")
        with self.assertRaises(StorageError):
            self.knowledge.add_segment_reference(segment, one, "@视频1")
        note = self.knowledge.add_ps_note("c1", "口袋", "多余装饰")
        self.knowledge.add_ps_asset(note, one, "before")
        with self.assertRaises(StorageError):
            self.knowledge.add_ps_asset(note, other, "after")



if __name__ == "__main__":
    unittest.main()
