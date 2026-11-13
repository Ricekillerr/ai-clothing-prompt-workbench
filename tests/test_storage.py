# -*- coding: utf-8 -*-
"""Offline regression tests for Prompt Atelier storage.py."""
import hashlib
import json
import sqlite3
import tempfile
import zipfile
import unittest
from pathlib import Path

from storage import Storage, StorageError


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "profile"
        self.home.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write_legacy(self, payload):
        path = self.home / "prompt_library.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_migrates_cases_and_modules_without_modifying_source(self):
        original = {"version": 2, "cases": [{"id": "c1", "name": "case", "body": "提示词"}],
                    "modules": [{"id": "m1", "name": "fabric", "body": "面料"}]}
        path = self.write_legacy(original)
        before = path.read_bytes()
        store = Storage(self.home)
        loaded = store.load_data()
        self.assertEqual([x["id"] for x in loaded["cases"]], ["c1"])
        self.assertEqual([x["id"] for x in loaded["modules"]], ["m1"])
        self.assertEqual(path.read_bytes(), before)

    def test_corrupt_json_does_not_create_empty_database(self):
        path = self.home / "prompt_library.json"
        path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(StorageError):
            Storage(self.home)
        self.assertFalse((self.home / "prompt_library.sqlite3").exists())
        self.assertTrue(path.exists())

    def test_duplicate_or_missing_ids_are_rejected_before_database_creation(self):
        for payload in (
            {"cases": [{"id": "x"}], "modules": [{"id": "x"}]},
            {"cases": [{"name": "missing"}], "modules": []},
        ):
            with self.subTest(payload=payload):
                self.write_legacy(payload)
                db = self.home / "prompt_library.sqlite3"
                if db.exists():
                    db.unlink()
                with self.assertRaises(StorageError):
                    Storage(self.home)
                self.assertFalse(db.exists())

    def test_save_load_round_trip_and_backup(self):
        store = Storage(self.home)
        payload = {"cases": [{"id": "c1", "name": "test", "body": "original"}],
                   "modules": [{"id": "m1", "name": "module", "body": "fabric"}]}
        store.save_data(payload)
        self.assertEqual(store.load_data()["cases"][0]["body"], "original")
        backup = store.backup()
        self.assertTrue(backup.exists())

    def test_invalid_save_does_not_delete_existing_data(self):
        store = Storage(self.home)
        store.save_data({"cases": [{"id": "c1", "body": "safe"}], "modules": []})
        with self.assertRaises(StorageError):
            store.save_data({"cases": [{"body": "missing id"}], "modules": []})
        self.assertEqual(store.load_data()["cases"][0]["body"], "safe")

    def test_existing_attachment_link_survives_normal_save(self):
        store = Storage(self.home)
        store.save_data({"cases": [{"id": "c1", "body": "before"}], "modules": []})
        with store._session() as con:
            con.execute(
                "INSERT INTO attachments(asset_id,relative_path,media_type,role) VALUES(?,?,?,?)",
                ("c1", "references/example.png", "image", "reference"),
            )
        store.save_data({"cases": [{"id": "c1", "body": "after"}], "modules": []})
        with store._session() as con:
            row = con.execute("SELECT relative_path FROM attachments WHERE asset_id='c1'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["relative_path"], "references/example.png")

    def test_export_json_contains_round_trip_data(self):
        store = Storage(self.home)
        store.save_data({"cases": [{"id": "c1", "body": "中文提示词"}], "modules": []})
        output = self.home / "exports" / "backup.json"
        store.export_json(output)
        exported = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(exported["cases"][0]["body"], "中文提示词")

    def test_interrupted_first_migration_is_retried_on_next_start(self):
        source = self.write_legacy({
            "cases": [{"id": "c-retry", "body": "恢复迁移"}],
            "modules": [{"id": "m-retry", "body": "模块"}],
        })
        # Simulate a database whose schema exists but migration marker was not committed.
        import sqlite3
        db = self.home / "prompt_library.sqlite3"
        con = sqlite3.connect(db)
        con.executescript("""
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE assets (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL CHECK(kind IN ('case','module')),
                payload_json TEXT NOT NULL,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                relative_path TEXT NOT NULL,
                media_type TEXT NOT NULL DEFAULT 'image',
                role TEXT NOT NULL DEFAULT 'reference',
                sha256 TEXT,
                created_at TEXT,
                UNIQUE(asset_id, relative_path, role)
            );
            INSERT INTO metadata(key,value) VALUES('schema_version','1');
        """)
        con.commit()
        con.close()
        store = Storage(self.home, legacy_json=source)
        loaded = store.load_data()
        self.assertEqual(loaded["cases"][0]["id"], "c-retry")
        self.assertEqual(loaded["modules"][0]["id"], "m-retry")

    def test_duplicate_ids_are_rejected_on_save(self):
        store = Storage(self.home)
        with self.assertRaises(StorageError):
            store.save_data({
                "cases": [{"id": "same"}],
                "modules": [{"id": "same"}],
            })
        self.assertEqual(store.load_data()["cases"], [])

    def test_scan_source_and_ingest_queue_preserve_approved_unchanged_file(self):
        store = Storage(self.home)
        source_dir = self.home / "history"
        source_dir.mkdir()
        source_id = store.register_scan_source(str(source_dir))
        item = {
            "path": str(source_dir / "sample.png"),
            "relative_path": "sample.png",
            "name": "sample.png",
            "extension": ".png",
            "media_type": "image",
            "size": 12,
            "modified_ns": 123,
            "sha256": "a" * 64,
        }
        first = store.save_scan_results(source_id, [item])
        self.assertEqual(first["added"], 1)
        self.assertEqual(len(store.list_pending_ingest(source_id)), 1)
        store.set_ingest_status(item["path"], "approved")
        second = store.save_scan_results(source_id, [item])
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(store.list_pending_ingest(source_id), [], "已审核且内容未变化的文件不应重新进入待审核队列")

    def test_changed_ingest_content_returns_to_pending(self):
        store = Storage(self.home)
        source_dir = self.home / "history"
        source_dir.mkdir()
        source_id = store.register_scan_source(str(source_dir))
        item = {
            "path": str(source_dir / "prompt.txt"),
            "relative_path": "prompt.txt",
            "name": "prompt.txt",
            "extension": ".txt",
            "media_type": "text",
            "size": 3,
            "modified_ns": 1,
            "sha256": "b" * 64,
        }
        store.save_scan_results(source_id, [item])
        store.set_ingest_status(item["path"], "ignored")
        changed = dict(item, size=4, modified_ns=2, sha256="c" * 64)
        result = store.save_scan_results(source_id, [changed])
        self.assertEqual(result["changed"], 1)
        self.assertEqual(len(store.list_pending_ingest(source_id)), 1)

    def test_ai_analysis_requires_human_approval_before_asset_import(self):
        store = Storage(self.home)
        source_dir = self.home / "history"
        source_dir.mkdir()
        store.register_scan_source(str(source_dir))
        prompt_file = source_dir / "look.txt"
        image_front = source_dir / "look_front.png"
        image_back = source_dir / "look_back.png"
        prompt_file.write_text("保持人物不变，仅替换服装", encoding="utf-8")
        image_front.write_bytes(b"front-image")
        image_back.write_bytes(b"back-image")
        prompt_hash = hashlib.sha256(prompt_file.read_bytes()).hexdigest()
        asset_paths = [str(image_front), str(image_back)]
        asset_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (image_front, image_back)]
        analysis_id = store.save_ai_analysis(
            str(prompt_file), str(image_front), prompt_hash, asset_hashes[0], "test-model",
            {"title": "Look test", "category": "服装", "tags": ["黑色"],
             "prompt_text": "保持人物不变，仅替换服装", "prompt_summary": "局部换装",
             "confidence": 0.92, "risk_notes": [],
             "asset_paths": asset_paths, "asset_sha256s": asset_hashes,
             "asset_roles": [
                 {"asset_index": 1, "asset_path": asset_paths[0], "role": "front",
                  "role_label": "正面图", "priority": "primary",
                  "governs": ["结构", "版型"], "observation": "正面整体版型"},
                 {"asset_index": 2, "asset_path": asset_paths[1], "role": "back",
                  "role_label": "背面图", "observation": "背面结构"},
             ],
             "outfit_items": [
                 {"item_index": 1, "category": "上装", "name": "黑色马甲",
                  "material": "尼龙", "color": "黑色", "details": ["立领"],
                  "evidence": "正面图片可见", "confidence": 0.9},
                 {"item_index": 2, "category": "下装", "name": "深灰裤子",
                  "material": "灯芯绒", "color": "深灰", "details": [],
                  "evidence": "整体图可见", "confidence": 0.8},
             ],
             "outfit_relations": [
                 {"source_item": 1, "target_item": 2, "relation": "paired_with",
                  "evidence": "整套搭配参考图"}
             ]},
        )
        self.assertEqual(len(store.list_ai_analyses("review")), 1)
        self.assertEqual(store.load_data()["cases"], [])
        asset_id = store.approve_ai_analysis(analysis_id)
        cases = store.load_data()["cases"]
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["id"], asset_id)
        self.assertEqual(cases[0]["body"], "保持人物不变，仅替换服装")
        provenance = cases[0]["provenance"]
        self.assertEqual(provenance["type"], "ai_history_import")
        self.assertEqual(provenance["analysis_id"], analysis_id)
        self.assertEqual(provenance["model"], "test-model")
        self.assertEqual(provenance["source_prompt"]["path"], str(prompt_file.resolve()))
        self.assertEqual(provenance["source_prompt"]["sha256"], prompt_hash)
        self.assertEqual(len(provenance["source_assets"]), 2)
        self.assertEqual(
            [x["sha256"] for x in provenance["source_assets"]], asset_hashes
        )
        self.assertTrue(all(Path(x["path"]).is_file()
                            for x in provenance["source_assets"]))
        self.assertEqual(len(cases[0]["images"]), 2)
        self.assertEqual([x["role"] for x in cases[0]["image_roles"]], ["front", "back"])
        self.assertEqual([x["role_label"] for x in cases[0]["image_roles"]], ["正面图", "背面图"])
        self.assertEqual(cases[0]["image_roles"][1]["observation"], "背面结构")
        self.assertEqual(cases[0]["image_roles"][0]["priority"], "primary")
        self.assertEqual(cases[0]["image_roles"][0]["governs"], ["结构", "版型"])
        self.assertEqual([x["name"] for x in cases[0]["outfit_items"]], ["黑色马甲", "深灰裤子"])
        self.assertEqual(cases[0]["outfit_items"][0]["material"], "尼龙")
        self.assertEqual(cases[0]["outfit_relations"][0]["relation"], "paired_with")
        self.assertTrue(all((self.home / "references" / name).is_file() for name in cases[0]["images"]))
        self.assertEqual(store.list_ai_analyses("review"), [])
        self.assertEqual(store.list_ai_analyses("approved")[0]["approved_asset_id"], asset_id)

    def test_ai_analysis_can_be_rejected_without_import(self):
        store = Storage(self.home)
        analysis_id = store.save_ai_analysis(
            "prompt.txt", "asset.png", "p" * 64, "i" * 64, "test-model",
            {"title": "Rejected", "prompt_text": "text"},
        )
        store.reject_ai_analysis(analysis_id)
        self.assertEqual(store.load_data()["cases"], [])
        self.assertEqual(store.list_ai_analyses("rejected")[0]["id"], analysis_id)

    def test_asset_edits_create_revisions_and_can_be_restored(self):
        store = Storage(self.home)
        original = {"cases": [{"id": "case-history", "name": "Before", "body": "prompt v1"}],
                    "modules": []}
        store.save_data(original)
        changed = {"cases": [{"id": "case-history", "name": "After", "body": "prompt v2"}],
                   "modules": []}
        store.save_data(changed)
        revisions = store.list_asset_revisions("case-history")
        self.assertEqual(len(revisions), 1)
        self.assertEqual(revisions[0]["name"], "Before")
        historical = store.get_asset_revision("case-history", revisions[0]["revision_id"])
        self.assertEqual(historical["asset"]["body"], "prompt v1")
        self.assertEqual(historical["reason"], "edited")
        restored = store.restore_asset_revision("case-history", revisions[0]["revision_id"])
        self.assertEqual(restored["name"], "Before")
        self.assertEqual(restored["body"], "prompt v1")
        self.assertEqual(store.load_data()["cases"][0]["name"], "Before")
        self.assertTrue(any(x["reason"] == "before_restore"
                            for x in store.list_asset_revisions("case-history")))

    def test_deleted_asset_can_be_recovered_from_its_last_revision(self):
        store = Storage(self.home)
        store.save_data({"cases": [{"id": "deleted-case", "name": "Recover me", "body": "prompt"}],
                         "modules": []})
        store.save_data({"cases": [], "modules": []})
        revisions = store.list_asset_revisions("deleted-case")
        deleted = next(x for x in revisions if x["reason"] == "deleted")
        restored = store.restore_asset_revision("deleted-case", deleted["revision_id"])
        self.assertEqual(restored["name"], "Recover me")
        self.assertEqual(store.load_data()["cases"][0]["id"], "deleted-case")

    def test_full_backup_restore_replaces_database_json_and_references(self):
        store = Storage(self.home)
        store.save_data({"cases": [{"id": "before", "name": "Before", "body": "old prompt"}],
                         "modules": []})
        references = self.home / "references"
        references.mkdir(exist_ok=True)
        (references / "before.png").write_bytes(b"before-image")
        backup = store.create_full_backup(references, app_version="0.6.5")

        store.save_data({"cases": [{"id": "after", "name": "After", "body": "new prompt"}],
                         "modules": []})
        (references / "after.png").write_bytes(b"after-image")
        result = store.restore_full_backup(backup)

        self.assertTrue(result["ok"])
        self.assertTrue((self.home / "backups" / result["safety_backup"]).is_file())
        self.assertEqual(result["cases"], 1)
        self.assertEqual(store.load_data()["cases"][0]["id"], "before")
        self.assertEqual(store.load_data()["cases"][0]["body"], "old prompt")
        self.assertTrue((references / "before.png").is_file())
        self.assertFalse((references / "after.png").exists())
        self.assertTrue(store.legacy_json.is_file())

    def test_corrupt_full_backup_is_rejected_without_changing_live_data(self):
        store = Storage(self.home)
        references = self.home / "references"
        references.mkdir(exist_ok=True)
        (references / "current.png").write_bytes(b"current-image")
        store.save_data({"cases": [{"id": "current", "name": "Current", "body": "keep me"}],
                         "modules": []})
        good = store.create_full_backup(references, app_version="0.6.5")
        corrupt = self.home / "corrupt_backup.zip"
        with zipfile.ZipFile(good, "r") as source, zipfile.ZipFile(corrupt, "w") as target:
            for info in source.infolist():
                payload = source.read(info.filename)
                if info.filename == "data/prompt_library.json":
                    payload += b"tampered"
                target.writestr(info, payload)
        with self.assertRaisesRegex(StorageError, "SHA-256"):
            store.restore_full_backup(corrupt)
        self.assertEqual(store.load_data()["cases"][0]["id"], "current")
        self.assertEqual((references / "current.png").read_bytes(), b"current-image")

    def test_full_backup_restore_rejects_zip_path_traversal(self):
        store = Storage(self.home)
        references = self.home / "references"
        references.mkdir(exist_ok=True)
        (references / "safe.png").write_bytes(b"safe")
        good = store.create_full_backup(references, app_version="0.6.5")
        malicious = self.home / "traversal_backup.zip"
        with zipfile.ZipFile(good, "r") as source:
            manifest = json.loads(source.read("backup_manifest.json").decode("utf-8"))
            manifest["files"]["../escape.txt"] = hashlib.sha256(b"escape").hexdigest()
            with zipfile.ZipFile(malicious, "w") as target:
                for info in source.infolist():
                    if info.filename == "backup_manifest.json":
                        continue
                    target.writestr(info, source.read(info.filename))
                target.writestr("../escape.txt", b"escape")
                target.writestr("backup_manifest.json", json.dumps(manifest))
        with self.assertRaisesRegex(StorageError, "不安全的文件路径"):
            store.restore_full_backup(malicious)
        self.assertFalse((self.home / "escape.txt").exists())
        self.assertFalse((self.home.parent / "escape.txt").exists())

    def test_retrieval_feedback_is_local_bounded_and_aggregated(self):
        store = Storage(self.home)
        store.save_data({"cases": [{"id": "feedback-case", "name": "Feedback", "body": "prompt"}],
                         "modules": []})
        store.record_retrieval_feedback("黑色尼龙马甲", "feedback-case", 0.92, "useful")
        store.record_retrieval_feedback("红色皮革裤子", "feedback-case", 0.35, "not_relevant")
        analytics = store.get_retrieval_analytics()
        self.assertEqual(analytics["total_feedback"], 2)
        self.assertEqual(analytics["useful"], 1)
        self.assertEqual(analytics["not_relevant"], 1)
        self.assertEqual(analytics["top_cases"][0]["case_id"], "feedback-case")

    def test_retrieval_feedback_rejects_unknown_case_and_invalid_kind(self):
        store = Storage(self.home)
        with self.assertRaisesRegex(Exception, "找不到对应的案例"):
            store.record_retrieval_feedback("query", "missing-case", 0.5, "useful")
        store.save_data({"cases": [{"id": "feedback-case", "name": "Feedback", "body": "prompt"}],
                         "modules": []})
        with self.assertRaisesRegex(Exception, "反馈类型"):
            store.record_retrieval_feedback("query", "feedback-case", 0.5, "auto_upgrade")

    def test_local_settings_persist_in_sqlite_metadata(self):
        store = Storage(self.home)
        self.assertEqual(store.get_setting("auto_scan_enabled", "0"), "0")
        store.set_setting("auto_scan_enabled", "1")
        self.assertEqual(store.get_setting("auto_scan_enabled"), "1")
        store.set_setting("auto_scan_enabled", "0")
        self.assertEqual(store.get_setting("auto_scan_enabled"), "0")

    def test_full_backup_contains_verified_database_json_and_references(self):
        store = Storage(self.home)
        store.save_data({"cases": [{"id": "case-1", "name": "Test"}],
                         "modules": [{"id": "module-1", "name": "Module"}]})
        references = self.home / "references"
        references.mkdir()
        image = references / "ref.png"
        image.write_bytes(b"fake-image-content")

        archive_path = store.create_full_backup(references, app_version="0.6.5-test")
        self.assertTrue(archive_path.is_file())
        with zipfile.ZipFile(archive_path) as archive:
            self.assertIsNone(archive.testzip())
            names = set(archive.namelist())
            self.assertEqual(names, {
                "database/prompt_library.sqlite3",
                "data/prompt_library.json",
                "references/ref.png",
                "backup_manifest.json",
            })
            manifest = json.loads(archive.read("backup_manifest.json"))
            self.assertEqual(manifest["format"], "PromptAtelierFullBackup")
            self.assertEqual(manifest["app_version"], "0.6.5-test")
            for name, expected in manifest["files"].items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), expected)
            db_path = self.home / "restored_test.sqlite3"
            db_path.write_bytes(archive.read("database/prompt_library.sqlite3"))
        con = sqlite3.connect(db_path)
        try:
            self.assertEqual(con.execute("PRAGMA quick_check").fetchone()[0], "ok")
        finally:
            con.close()
        self.assertEqual(image.read_bytes(), b"fake-image-content")


if __name__ == "__main__":
    unittest.main(verbosity=2)
