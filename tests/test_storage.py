import csv
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import storage as storage_module
from storage import (
    BackupManifest, 
    Storage,
    StorageConflictError,
    StorageValidationError,
    create_asset_id,
    is_windows_reserved_name,
    merge_asset_history,
    merge_asset_records,
    merge_case_and_module_records,
    normalize_asset_tags,
    normalize_outfit_relations,
    normalize_image_roles,
    normalize_image_role,
    normalize_outfit_items,
    normalize_provenance,
    normalize_structured_attributes,
    revision_snapshot,
    sanitize_backup_zip_name,
    validate_restore_paths,
)


def _test_revision_snapshot(asset):
    """Test-only snapshot with a deterministic timestamp."""
    snapshot = revision_snapshot(asset)
    snapshot["saved_at"] = "2026-11-13T00:00:00+00:00"
    return snapshot


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.store = Storage(self.root)

    def tearDown(self):
        # sqlite3.Connection does not implement __del__; close captured error
        # connections explicitly so Windows can release WAL/database handles.
        for name in ("conn", "delete_conn"):
            connection = locals().get(name)
            close = getattr(connection, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        self.tempdir.cleanup()

    def test_legacy_json_is_migrated_once_and_backed_up(self):
        legacy = {"version": "0.4.2", "cases": [{"id": "case-1", "name": "夹克", "body": "提示词"}]}
        legacy_path = self.root / "prompt_library.json"
        legacy_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        other = Storage(self.root)
        data = other.load_data()
        self.assertEqual(data["cases"][0]["id"], "case-1")
        self.assertEqual(other.counts()["cases"], 1)
        self.assertTrue(legacy_path.exists())
        self.assertTrue(any(self.root.joinpath("legacy_json").glob("prompt_library_*.json")))

    def test_save_data_rewrites_cases_and_creates_revisions(self):
        record = {"id": "case-1", "name": "夹克", "body": "v1", "tags": ["服装"]}
        self.store.save_data({"cases": [record], "modules": []})
        record["body"] = "v2"
        self.store.save_data({"cases": [record], "modules": []})
        self.assertEqual(self.store.load_data()["cases"][0]["body"], "v2")
        revisions = self.store.list_asset_revisions("case-1")
        self.assertEqual(len(revisions), 1)
        self.assertEqual(revisions[0]["payload"]["body"], "v1")

    def test_save_data_is_atomic_when_revision_insert_fails(self):
        record = {"id": "case-1", "name": "夹克", "body": "v1"}
        self.store.save_data({"cases": [record], "modules": []})
        record["body"] = "v2"
        with mock.patch.object(self.store, "_insert_revision", side_effect=sqlite3.OperationalError("fail")):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.save_data({"cases": [record], "modules": []})
        self.assertEqual(self.store.load_data()["cases"][0]["body"], "v1")
        self.assertEqual(len(self.store.list_asset_revisions("case-1")), 0)

    def test_scan_results_are_idempotent_and_review_can_be_set(self):
        source_id = self.store.register_scan_source(str(self.root), recursive=True)
        text = self.root / "prompt.txt"
        text.write_text("sample prompt", encoding="utf-8")
        file_hash = hashlib.sha256(text.read_bytes()).hexdigest()
        items = [{"path": str(text), "name": "prompt.txt", "media_type": "text",
                  "size": text.stat().st_size, "mtime": text.stat().st_mtime, "sha256": file_hash}]
        first = self.store.save_scan_results(source_id, items)
        self.assertEqual(first["added"], 1)
        self.assertEqual(self.store.save_scan_results(source_id, items)["unchanged"], 1)
        self.assertEqual(self.store.set_ingest_status(str(text), "approved"), "approved")
        pending = self.store.list_pending_ingest()
        self.assertEqual(pending, [])
        self.assertEqual(self.store.list_pending_ingest()[0:0], [])

    def test_scan_results_handle_paths_that_differ_only_by_unicode_normalization(self):
        source_id = self.store.register_scan_source(str(self.root), recursive=True)
        records = [
            {"path": str(self.root / "\u0063\u0301.txt"), "name": "one", "media_type": "text",
             "size": 1, "mtime": 1.0, "sha256": "a" * 64},
            {"path": str(self.root / "\u00e7.txt"), "name": "two", "media_type": "text",
             "size": 2, "mtime": 2.0, "sha256": "b" * 64},
        ]
        summary = self.store.save_scan_results(source_id, records)
        self.assertEqual(summary["added"], 2)
        self.assertEqual(summary["unchanged"], 0)
        self.assertEqual(len(self.store.list_pending_ingest()), 2)

    def test_scan_replaces_stale_records_for_one_source_only(self):
        one = self.store.register_scan_source(str(self.root / "one"))
        two = self.store.register_scan_source(str(self.root / "two"))
        for source_id in (one, two):
            self.store.save_scan_results(source_id, [{"path": str(self.root / f"{source_id}.txt"),
                "name": f"{source_id}.txt", "media_type": "text", "size": 1, "mtime": 1.0, "sha256": "x" * 64}])
        self.store.save_scan_results(one, [])
        rows = self.store.list_pending_ingest()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_id"], two)

    def test_manual_rescan_can_recompute_hash_even_when_metadata_is_unchanged(self):
        source_id = self.store.register_scan_source(str(self.root))
        path = self.root / "prompt.txt"
        path.write_text("original", encoding="utf-8")
        info = path.stat()
        original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        item = {"path": str(path), "name": "prompt.txt", "media_type": "text",
                "size": info.st_size, "mtime": info.st_mtime, "sha256": original_hash}
        self.store.save_scan_results(source_id, [item])
        path.write_text("tampered", encoding="utf-8")
        path.chmod(info.st_mode)
        path.touch()
        path.chmod(info.st_mode)
        # Force identical stat metadata while changing the bytes.
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        item["sha256"] = original_hash
        self.store.save_scan_results(source_id, [item])
        self.assertEqual(self.store.get_ingest_record(str(path))["sha256"], original_hash)
        self.store.save_scan_results(source_id, [item], force_hash=True)
        self.assertEqual(self.store.get_ingest_record(str(path))["sha256"],
                         hashlib.sha256(b"tampered").hexdigest())

    def test_metadata_only_scan_does_not_read_full_file(self):
        source_id = self.store.register_scan_source(str(self.root))
        path = self.root / "prompt.txt"
        path.write_text("original", encoding="utf-8")
        info = path.stat()
        original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        item = {"path": str(path), "name": "prompt.txt", "media_type": "text",
                "size": info.st_size, "mtime": info.st_mtime, "sha256": original_hash}
        self.store.save_scan_results(source_id, [item])
        with mock.patch.object(storage_module, "sha256_file",
                               side_effect=AssertionError("must not hash unchanged metadata")):
            self.store.save_scan_results(source_id, [item])
        self.store.save_scan_results(source_id, [item], force_hash=True)

    def test_review_status_is_constrained(self):
        with self.assertRaises(ValueError):
            self.store.set_ingest_status("not-exists", "deleted")

    def test_full_backup_contains_database_manifest_and_references(self):
        self.store.save_data({"cases": [{"id": "case-1", "name": "夹克", "body": "提示词"}], "modules": []})
        image = self.root / "references"
        image.mkdir()
        (image / "one.png").write_bytes(b"image")
        archive = self.store.create_full_backup(image, app_version="0.5.0")
        self.assertTrue(archive.is_file())
        with zipfile.ZipFile(archive) as zf:
            names = set(zf.namelist())
            self.assertIn("prompt_atelier.db", names)
            self.assertIn("backup_manifest.json", names)
            self.assertIn("references/one.png", names)
            manifest = json.loads(zf.read("backup_manifest.json"))
            self.assertEqual(manifest["format"], "prompt_atelier_full_backup")
            self.assertEqual(manifest["format_version"], 1)
            self.assertEqual(manifest["files"]["references/one.png"]["sha256"], hashlib.sha256(b"image").hexdigest())
            self.assertNotIn("legacy_json/prompt_library.json", names)

    def test_restore_full_backup_replaces_only_storage_and_references(self):
        self.store.save_data({"cases": [{"id": "old", "body": "old"}], "modules": []})
        (self.root / "references").mkdir()
        (self.root / "references" / "old.png").write_bytes(b"old")
        archive = self.store.create_full_backup(self.root / "references", app_version="0.5.0")
        self.store.save_data({"cases": [{"id": "new", "body": "new"}], "modules": []})
        result = self.store.restore_full_backup(archive)
        self.assertTrue(result["ok"])
        self.assertEqual(self.store.load_data()["cases"][0]["id"], "old")
        self.assertTrue((self.root / "references" / "old.png").exists())

    def test_restore_rejects_wrong_manifest_format_version_and_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "prompt_atelier.db"
            db.write_bytes(b"sqlite-test")
            tests = [
                ({"format": "wrong", "format_version": 1, "files": {"prompt_atelier.db": {"sha256": hashlib.sha256(b"sqlite-test").hexdigest()}}}, "不支持"),
                ({"format": "prompt_atelier_full_backup", "format_version": 999, "files": {"prompt_atelier.db": {"sha256": hashlib.sha256(b"sqlite-test").hexdigest()}}}, "不支持"),
                ({"format": "prompt_atelier_full_backup", "format_version": 1, "files": {"prompt_atelier.db": {"sha256": "0" * 64}}}, "校验"),
            ]
            for manifest, message in tests:
                archive = root / ("bad-" + message + ".zip")
                with zipfile.ZipFile(archive, "w") as zf:
                    zf.write(db, "prompt_atelier.db")
                    zf.writestr("backup_manifest.json", json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, message):
                    self.store.restore_full_backup(archive)

    def test_restore_rejects_unexpected_paths_and_symlinks(self):
        archive = self.root / "bad.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("backup_manifest.json", json.dumps({"format": "prompt_atelier_full_backup", "format_version": 1,
                "files": {"prompt_atelier.db": {"size": 0, "sha256": "0" * 64}}}))
            zf.writestr("../../outside.txt", "bad")
        with self.assertRaises(ValueError):
            self.store.restore_full_backup(archive)
        symlink = zipfile.ZipInfo("references/link")
        symlink.create_system = 3
        symlink.external_attr = (0o120777 << 16)
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("backup_manifest.json", json.dumps({"format": "prompt_atelier_full_backup", "format_version": 1,
                "files": {"prompt_atelier.db": {"size": 0, "sha256": "0" * 64}}}))
            zf.writestr(symlink, "target")
        with self.assertRaises(ValueError):
            self.store.restore_full_backup(archive)

    def test_restore_rolls_back_if_old_home_cannot_be_moved(self):
        self.store.save_data({"cases": [{"id": "keep", "body": "keep"}], "modules": []})
        self.assertEqual(self.store.counts()["cases"], 1)
        archive = self.store.create_full_backup(self.root / "references", app_version="0.5.0")
        self.store.save_data({"cases": [{"id": "changed", "body": "changed"}], "modules": []})
        self.assertEqual(self.store.load_data()["cases"][0]["id"], "changed")
        with mock.patch.object(self.store, "_open", side_effect=RuntimeError("restore failed")):
            with self.assertRaises(RuntimeError):
                self.store.restore_full_backup(archive)
        self.assertEqual(self.store.load_data()["cases"][0]["id"], "changed")

    def test_restore_rollbacks_if_references_cannot_move_back(self):
        self.store.save_data({"cases": [{"id": "keep", "body": "keep"}], "modules": []})
        archive = self.store.create_full_backup(self.root / "references", app_version="0.5.0")
        original = os.replace
        calls = {"n": 0}
        def flaky_replace(a, b):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("simulated move failure")
            return original(a, b)
        with mock.patch("storage.os.replace", side_effect=flaky_replace):
            with self.assertRaises(OSError):
                self.store.restore_full_backup(archive)
        self.assertEqual(self.store.counts()["cases"], 1)
        self.assertEqual(self.store.load_data()["cases"][0]["id"], "keep")

    def test_scan_and_ai_routes_have_hash_provenance(self):
        source_id = self.store.register_scan_source(str(self.root))
        prompt = self.root / "p.txt"
        image = self.root / "a.png"
        prompt.write_text("prompt", encoding="utf-8")
        image.write_bytes(b"png")
        for path, media in ((prompt, "text"), (image, "image")):
            self.store.save_scan_results(source_id, [{"path": str(path), "name": path.name, "media_type": media,
                "size": path.stat().st_size, "mtime": path.stat().st_mtime,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}])
        analysis = {"model": "vision-test", "title": "T", "category": "上装", "material": "棉", "color": "黑", "tags": [], "prompt_text": "prompt", "prompt_text_original": "prompt"}
        analysis_id = self.store.save_ai_analysis(str(prompt), str(image), hashlib.sha256(b"prompt").hexdigest(), hashlib.sha256(b"png").hexdigest(), "vision-test", analysis)
        review = self.store.list_ai_analyses("review")[0]
        self.assertEqual(review["id"], analysis_id)
        self.assertEqual(review["prompt_sha256"], hashlib.sha256(b"prompt").hexdigest())
        self.assertEqual(review["analysis"]["prompt_text"], "prompt")
        self.store.reject_ai_analysis(analysis_id)
        self.assertEqual(self.store.list_ai_analyses("review"), [])

    def test_ai_approval_is_idempotent_and_records_hash_provenance(self):
        source_id = self.store.register_scan_source(str(self.root))
        prompt = self.root / "p.txt"
        image = self.root / "a.png"
        prompt.write_text("prompt", encoding="utf-8")
        image.write_bytes(b"png")
        records = []
        for path, media in ((prompt, "text"), (image, "image")):
            record = {"path": str(path), "name": path.name, "media_type": media,
                "size": path.stat().st_size, "mtime": path.stat().st_mtime,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            records.append(record)
        self.store.save_scan_results(source_id, records)
        analysis = {"model": "vision-test", "title": "T", "category": "上装", "material": "棉", "color": "黑", "tags": [], "prompt_text": "prompt", "prompt_text_original": "prompt"}
        analysis_id = self.store.save_ai_analysis(str(prompt), str(image), records[0]["sha256"], records[1]["sha256"], "vision-test", analysis)
        asset_id = self.store.approve_ai_analysis(analysis_id)
        self.assertEqual(self.store.approve_ai_analysis(analysis_id), asset_id)
        record = self.store.load_data()["cases"][0]
        self.assertEqual(record["image"], records[1]["sha256"] + ".png")
        self.assertEqual(record["source_assets"][0]["sha256"], records[1]["sha256"])
        self.assertEqual(self.store.get_ingest_record(str(prompt))["status"], "imported")
        self.assertEqual(self.store.get_ingest_record(str(image))["status"], "imported")

    def test_ai_approval_rechecks_all_asset_hashes_and_copies_by_hash(self):
        source_id = self.store.register_scan_source(str(self.root))
        prompt = self.root / "p.txt"
        first = self.root / "a.png"
        second = self.root / "b.jpg"
        prompt.write_text("prompt", encoding="utf-8")
        first.write_bytes(b"png")
        second.write_bytes(b"jpg")
        records = []
        for path, media in ((prompt, "text"), (first, "image"), (second, "image")):
            records.append({"path": str(path), "name": path.name, "media_type": media,
                "size": path.stat().st_size, "mtime": path.stat().st_mtime,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        self.store.save_scan_results(source_id, records)
        analysis = {"model": "vision-test", "title": "T", "prompt_text": "prompt",
                    "prompt_text_original": "prompt", "tags": [],
                    "asset_paths": [str(first), str(second)]}
        analysis_id = self.store.save_ai_analysis(
            str(prompt), str(first), records[0]["sha256"], records[1]["sha256"], "vision-test", analysis
        )
        asset_id = self.store.approve_ai_analysis(analysis_id)
        record = self.store.load_data()["cases"][0]
        self.assertEqual(record["id"], asset_id)
        self.assertEqual(record["images"], [records[1]["sha256"] + ".png", records[2]["sha256"] + ".jpg"])
        self.assertEqual(len(record["source_assets"]), 2)
        self.assertEqual(record["source_assets"][1]["sha256"], records[2]["sha256"])
        self.assertTrue((self.root / "references" / record["images"][1]).exists())

    def test_ai_approval_refuses_modified_secondary_asset_and_retains_review(self):
        source_id = self.store.register_scan_source(str(self.root))
        prompt = self.root / "p.txt"
        first = self.root / "a.png"
        second = self.root / "b.png"
        prompt.write_text("prompt", encoding="utf-8")
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        records = [{"path": str(path), "name": path.name, "media_type": media,
                    "size": path.stat().st_size, "mtime": path.stat().st_mtime,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                   for path, media in ((prompt, "text"), (first, "image"), (second, "image"))]
        self.store.save_scan_results(source_id, records)
        analysis = {"model": "vision-test", "prompt_text": "prompt", "tags": [],
                    "asset_paths": [str(first), str(second)]}
        analysis_id = self.store.save_ai_analysis(str(prompt), str(first), records[0]["sha256"], records[1]["sha256"], "vision-test", analysis)
        second.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "哈希发生变化"):
            self.store.approve_ai_analysis(analysis_id)
        self.assertEqual(self.store.load_data()["cases"], [])
        self.assertEqual(self.store.list_ai_analyses("review")[0]["id"], analysis_id)

    def test_ai_approval_rejects_scan_root_with_symlink_component(self):
        real = self.root / "real"
        real.mkdir()
        link = self.root / "alias"
        try:
            os.symlink(real, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        source_id = self.store.register_scan_source(str(link))
        prompt = link / "p.txt"
        image = link / "a.png"
        prompt.write_text("prompt", encoding="utf-8")
        image.write_bytes(b"png")
        records = [{"path": str(path), "name": path.name, "media_type": media,
                    "size": path.stat().st_size, "mtime": path.stat().st_mtime,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                   for path, media in ((prompt, "text"), (image, "image"))]
        self.store.save_scan_results(source_id, records)
        analysis = {"model": "vision-test", "prompt_text": "prompt", "tags": [], "asset_paths": [str(image)]}
        analysis_id = self.store.save_ai_analysis(str(prompt), str(image), records[0]["sha256"], records[1]["sha256"], "vision-test", analysis)
        with self.assertRaisesRegex(ValueError, "符号链接或目录联接"):
            self.store.approve_ai_analysis(analysis_id)

    def test_ai_approval_rejects_prompt_hash_mismatch_even_when_original_text_is_retained(self):
        source_id = self.store.register_scan_source(str(self.root))
        prompt = self.root / "p.txt"
        image = self.root / "a.png"
        prompt.write_text("prompt", encoding="utf-8")
        image.write_bytes(b"png")
        records = [{"path": str(path), "name": path.name, "media_type": media,
                    "size": path.stat().st_size, "mtime": path.stat().st_mtime,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                   for path, media in ((prompt, "text"), (image, "image"))]
        self.store.save_scan_results(source_id, records)
        analysis = {"model": "vision-test", "prompt_text": "prompt", "prompt_text_original": "prompt", "tags": [], "asset_paths": [str(image)]}
        analysis_id = self.store.save_ai_analysis(str(prompt), str(image), records[0]["sha256"], records[1]["sha256"], "vision-test", analysis)
        prompt.write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "提示词哈希"):
            self.store.approve_ai_analysis(analysis_id)

    def test_ai_approval_cleans_copied_images_if_database_transaction_fails(self):
        source_id = self.store.register_scan_source(str(self.root))
        prompt = self.root / "p.txt"
        image = self.root / "a.png"
        prompt.write_text("prompt", encoding="utf-8")
        image.write_bytes(b"png")
        records = [{"path": str(path), "name": path.name, "media_type": media,
                    "size": path.stat().st_size, "mtime": path.stat().st_mtime,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                   for path, media in ((prompt, "text"), (image, "image"))]
        self.store.save_scan_results(source_id, records)
        analysis = {"model": "vision-test", "prompt_text": "prompt", "tags": [], "asset_paths": [str(image)]}
        analysis_id = self.store.save_ai_analysis(str(prompt), str(image), records[0]["sha256"], records[1]["sha256"], "vision-test", analysis)
        with mock.patch.object(self.store, "_open", side_effect=RuntimeError("database unavailable")):
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                self.store.approve_ai_analysis(analysis_id)
        copied = self.root / "references" / (records[1]["sha256"] + ".png")
        self.assertFalse(copied.exists())
        self.assertEqual(self.store.load_data()["cases"], [])
        self.assertEqual(self.store.list_ai_analyses("review")[0]["id"], analysis_id)

    def test_ai_approval_rejects_symlink_reference_directory(self):
        source_id = self.store.register_scan_source(str(self.root))
        prompt = self.root / "p.txt"
        image = self.root / "a.png"
        prompt.write_text("prompt", encoding="utf-8")
        image.write_bytes(b"png")
        records = [{"path": str(path), "name": path.name, "media_type": media,
                    "size": path.stat().st_size, "mtime": path.stat().st_mtime,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                   for path, media in ((prompt, "text"), (image, "image"))]
        self.store.save_scan_results(source_id, records)
        analysis = {"model": "vision-test", "prompt_text": "prompt", "tags": [], "asset_paths": [str(image)]}
        analysis_id = self.store.save_ai_analysis(str(prompt), str(image), records[0]["sha256"], records[1]["sha256"], "vision-test", analysis)
        (self.root / "references").mkdir()
        (self.root / "references").rmdir()
        outside = self.root / "outside"
        outside.mkdir()
        try:
            os.symlink(outside, self.root / "references", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        with self.assertRaisesRegex(ValueError, "符号链接或目录联接"):
            self.store.approve_ai_analysis(analysis_id)
        self.assertFalse((outside / (records[1]["sha256"] + ".png")).exists())

    def test_ai_approval_recognizes_junction_like_reference_root_via_mock(self):
        source_id = self.store.register_scan_source(str(self.root))
        prompt = self.root / "p.txt"
        image = self.root / "a.png"
        prompt.write_text("prompt", encoding="utf-8")
        image.write_bytes(b"png")
        records = [{"path": str(path), "name": path.name, "media_type": media,
                    "size": path.stat().st_size, "mtime": path.stat().st_mtime,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                   for path, media in ((prompt, "text"), (image, "image"))]
        self.store.save_scan_results(source_id, records)
        analysis = {"model": "vision-test", "prompt_text": "prompt", "tags": [], "asset_paths": [str(image)]}
        analysis_id = self.store.save_ai_analysis(str(prompt), str(image), records[0]["sha256"], records[1]["sha256"], "vision-test", analysis)
        (self.root / "references").mkdir()
        with mock.patch("storage._is_link_or_junction", lambda path: Path(path) == self.root / "references"):
            with self.assertRaisesRegex(ValueError, "符号链接或目录联接"):
                self.store.approve_ai_analysis(analysis_id)

    def test_ai_analysis_rejects_empty_prompt(self):
        with self.assertRaises(ValueError):
            self.store.save_ai_analysis("", "", "a", "b", "m", {})

    def test_sqlite_integrity_check_detects_corruption(self):
        self.assertTrue(self.store.check_integrity()["ok"])
        with sqlite3.connect(self.root / "prompt_atelier.db") as conn:
            conn.execute("PRAGMA writable_schema=ON")
            conn.execute("UPDATE sqlite_master SET sql='broken' WHERE name='cases'")
            conn.commit()
        self.assertFalse(self.store.check_integrity()["ok"])

    def test_scan_register_rejects_file_instead_of_directory(self):
        file_path = self.root / "file.txt"
        file_path.write_text("x", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.register_scan_source(str(file_path))

    def test_scan_register_rejects_symlink_source(self):
        target = self.root / "real"
        target.mkdir()
        link = self.root / "link"
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        with self.assertRaises(ValueError):
            self.store.register_scan_source(str(link))

    def test_scan_register_recognizes_junction_via_path_is_junction(self):
        target = self.root / "junction-target"
        target.mkdir()
        link = self.root / "junction-alias"
        original = os.symlink if hasattr(os, "symlink") else None
        if original is None:
            self.skipTest("symlink creation unavailable")
        try:
            original(target, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        with self.assertRaises(ValueError):
            self.store.register_scan_source(str(link))

    def test_scan_metadata_collision_is_rolled_back(self):
        source_id = self.store.register_scan_source(str(self.root))
        path = self.root / "p.txt"
        path.write_text("x", encoding="utf-8")
        records = [{"path": str(path), "name": "p.txt", "media_type": "text",
                    "size": path.stat().st_size, "mtime": path.stat().st_mtime,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}]
        self.store.save_scan_results(source_id, records)
        changed = dict(records[0])
        changed["sha256"] = "f" * 64
        with sqlite3.connect(self.root / "prompt_atelier.db") as conn:
            conn.execute("CREATE TRIGGER block_update BEFORE UPDATE ON ingest_files BEGIN SELECT RAISE(ABORT, 'blocked'); END")
            conn.commit()
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                self.store.save_scan_results(source_id, [changed])
        finally:
            with sqlite3.connect(self.root / "prompt_atelier.db") as conn:
                conn.execute("DROP TRIGGER block_update")
                conn.commit()
        self.assertEqual(self.store.get_ingest_record(str(path))["sha256"], records[0]["sha256"])

    def test_ai_approval_rolls_back_all_changes_when_asset_is_invalid(self):
        source_id = self.store.register_scan_source(str(self.root))
        prompt = self.root / "p.txt"
        image = self.root / "a.png"
        prompt.write_text("prompt", encoding="utf-8")
        image.write_bytes(b"png")
        records = []
        for path, media in ((prompt, "text"), (image, "image")):
            records.append({"path": str(path), "name": path.name, "media_type": media,
                "size": path.stat().st_size, "mtime": path.stat().st_mtime,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        self.store.save_scan_results(source_id, records)
        analysis = {"model": "vision-test", "title": "T", "prompt_text": "prompt", "tags": [], "asset_paths": [str(image)]}
        analysis_id = self.store.save_ai_analysis(str(prompt), str(image), records[0]["sha256"], records[1]["sha256"], "vision-test", analysis)
        image.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.store.approve_ai_analysis(analysis_id)
        self.assertEqual(self.store.load_data()["cases"], [])
        self.assertEqual(self.store.get_ingest_record(str(prompt))["status"], "pending")
        self.assertEqual(self.store.list_ai_analyses("review")[0]["id"], analysis_id)



    def test_merge_asset_records_preserves_only_the_existing_asset_id(self):
        existing = {"id": "old-id", "name": "案例", "body": "old", "tags": ["a"]}
        incoming = {"id": "new-id", "name": "案例", "body": "new", "tags": ["b"]}
        merged = merge_asset_records(existing, incoming)
        self.assertEqual(merged["id"], "old-id")
        self.assertEqual(merged["body"], "new")
        self.assertEqual(merged["tags"], ["a", "b"])
        self.assertEqual(merge_asset_records(None, incoming)["id"], "new-id")

    def test_merge_case_and_module_records_accepts_nested_json_payloads(self):
        merged = merge_case_and_module_records(
            {"cases": [{"id": "case", "name": "case"}], "modules": []},
            {"cases": [{"id": "case", "name": "case", "body": "v2"}],
             "modules": [{"id": "module", "name": "module"}]},
        )
        self.assertEqual(merged["cases"][0]["body"], "v2")
        self.assertEqual(merged["modules"][0]["name"], "module")

    def test_merge_asset_history_is_deduplicated_by_revision_id_and_sorted(self):
        old = [{"revision_id": "old", "saved_at": "2026-11-13T00:00:00+00:00", "payload": {"body": "v1"}}]
        new = [{"revision_id": "old", "saved_at": "2026-11-13T00:00:00+00:00", "payload": {"body": "v1"}},
               {"revision_id": "new", "saved_at": "2026-11-14T00:00:00+00:00", "payload": {"body": "v2"}}]
        merged = merge_asset_history(old, new)
        self.assertEqual([item["revision_id"] for item in merged], ["new", "old"])
        self.assertEqual(len(merge_asset_history(old, new)), 2)

    def test_revision_snapshot_is_deterministic_and_json_safe(self):
        first = _test_revision_snapshot({"id": "x", "body": "提示词"})
        second = _test_revision_snapshot({"id": "x", "body": "提示词"})
        self.assertEqual(first, second)
        self.assertIn("saved_at", first)
        self.assertEqual(json.loads(json.dumps(first, ensure_ascii=False))["body"], "提示词")

    def test_create_asset_id_is_unique_for_two_calls(self):
        self.assertNotEqual(create_asset_id("case"), create_asset_id("case"))
        self.assertTrue(create_asset_id("module").startswith("module-"))

    def test_normalize_outfit_items_accepts_json_and_caps_unbounded_payloads(self):
        items, relations = normalize_outfit_items([
            {"category": "上装", "name": "外套", "confidence": 0.8},
            "not-an-item",
        ], [])
        self.assertEqual(items[0]["category"], "上装")
        self.assertEqual(items[0]["name"], "外套")
        self.assertEqual(relations, [])
        self.assertEqual(normalize_outfit_items('{"items":[{"name":"裤"}],"relations":[]}')[0][0]["name"], "裤")
        self.assertEqual(normalize_outfit_items(None)[0], [])

    def test_normalize_outfit_relations_drops_self_invalid_and_out_of_range_links(self):
        relations = normalize_outfit_relations([
            {"source_item": 1, "target_item": 2, "relation": "over"},
            {"source_item": 1, "target_item": 1, "relation": "over"},
            {"source_item": 1, "target_item": 4, "relation": "over"},
            {"source_item": 1, "target_item": 2, "relation": "unknown"},
        ], 2)
        self.assertEqual(relations, [{"source_item": 1, "target_item": 2,
                                      "relation": "over", "evidence": ""}])

    def test_normalize_image_roles_preserves_authority_and_governed_attributes(self):
        role = normalize_image_role({
            "asset_index": 1, "role": "detail", "role_label": "细节",
            "priority": "primary", "governs": ["细节", "工艺", 3, "细节"],
        }, 0, "one.png")
        self.assertEqual(role["role"], "detail")
        self.assertEqual(role["priority"], "primary")
        self.assertEqual(role["governs"], ["细节", "工艺"])
        roles = normalize_image_roles([
            {"asset_index": 2, "role": "back"},
            {"asset_index": 1, "role": "front"},
            {"asset_index": 99, "role": "label"},
        ], ["one.png", "two.png"])
        self.assertEqual([item["asset_path"] for item in roles], ["one.png", "two.png"])
        self.assertEqual(roles[1]["priority"], "primary")

    def test_normalize_structured_attributes_limits_scalar_and_list_shapes(self):
        result = normalize_structured_attributes({
            "silhouette": "A" * 1000,
            "details": ["拉链", "拉链", {"not": "text"}],
            "fabric": {"nested": "not allowed"},
        })
        self.assertEqual(len(result["silhouette"]), 1000)
        self.assertEqual(result["details"], ["拉链"])
        self.assertEqual(result["fabric"], "")

    def test_normalize_provenance_deduplicates_assets_and_normalizes_types(self):
        result = normalize_provenance({
            "source_assets": [
                {"path": "a.png", "sha256": "a" * 64},
                {"path": "a.png", "sha256": "a" * 64},
                {"path": "b.png", "sha256": "bad"},
            ],
            "composition": {"base_case_id": 12, "module_ids": ["a", "a"]},
        })
        self.assertEqual(result["source_assets"][0]["path"], "a.png")
        self.assertEqual(result["composition"]["base_case_id"], 12)
        self.assertEqual(result["composition"]["module_ids"], ["a"])
        self.assertEqual(result["source_assets"][1]["sha256"], "")

    def test_normalize_asset_tags_accepts_legacy_text_and_bounds_bad_shapes(self):
        self.assertEqual(normalize_asset_tags("服装"), ["服装"])
        self.assertEqual(normalize_asset_tags('{"bad": "json"'), ['{"bad": "json"'])
        self.assertEqual(normalize_asset_tags(None), [])
        self.assertEqual(normalize_asset_tags({"bad": True}), [])
        self.assertEqual(normalize_asset_tags([1, "服装", True, None, "服装"]), ["1", "服装"])
        self.assertEqual(normalize_asset_tags(["x" * 5000])[0], "x" * 200)

    def test_legacy_asset_with_null_tags_can_be_saved_without_crashing(self):
        self.store.save_data({"cases": [{"id": "legacy", "tags": None, "body": "x"}], "modules": []})
        self.store.save_data({"cases": [{"id": "legacy", "tags": "服装", "body": "x"}], "modules": []})
        self.assertEqual(self.store.load_data()["cases"][0]["tags"], ["服装"])

    def test_approve_ai_analysis_preserves_original_prompt_text_exactly(self):
        source_id = self.store.register_scan_source(str(self.root))
        prompt = self.root / "p.txt"
        image = self.root / "a.png"
        original = "\ufeff第一行\r\n  第二行  \r\n"
        prompt.write_bytes(original.encode("utf-8"))
        image.write_bytes(b"png")
        records = [{"path": str(path), "name": path.name, "media_type": media,
                    "size": path.stat().st_size, "mtime": path.stat().st_mtime,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                   for path, media in ((prompt, "text"), (image, "image"))]
        self.store.save_scan_results(source_id, records)
        analysis = {"model": "vision-test", "title": "T", "prompt_text": "模型改写文本",
                    "prompt_text_original": "模型改写文本", "tags": [],
                    "asset_paths": [str(image)]}
        analysis_id = self.store.save_ai_analysis(
            str(prompt), str(image), records[0]["sha256"], records[1]["sha256"],
            "vision-test", analysis
        )
        asset_id = self.store.approve_ai_analysis(analysis_id)
        record = self.store.load_data()["cases"][0]
        self.assertEqual(record["id"], asset_id)
        self.assertEqual(record["body"], original)

    def test_generation_outcome_records_transactional_feedback_revision_and_status(self):
        record = {"id": "case-outcome", "name": "测试", "body": "提示词", "status": "待验证"}
        self.store.save_data({"cases": [record], "modules": []})
        result = self.store.record_generation_outcome("case-outcome", "success", "实际生成成功")
        self.assertEqual(result["status"], "已验证成功")
        self.assertEqual(result["generation_successes"], 1)
        saved = self.store.load_data()["cases"][0]
        self.assertEqual(saved["status"], "已验证成功")
        self.assertEqual(saved["last_generation_outcome"], "success")
        self.assertEqual(saved["last_generation_notes"], "实际生成成功")
        with sqlite3.connect(self.root / "prompt_atelier.db") as conn:
            row = conn.execute(
                "SELECT outcome,notes,successes,failures FROM generation_feedback WHERE case_id=?",
                ("case-outcome",),
            ).fetchone()
            self.assertEqual(row, ("success", "实际生成成功", 1, 0))
        self.store.record_generation_outcome("case-outcome", "failed", "第二次失败")
        saved = self.store.load_data()["cases"][0]
        self.assertEqual(saved["status"], "失败案例")
        self.assertEqual(saved["generation_successes"], 1)
        self.assertEqual(saved["generation_failures"], 1)
        self.assertEqual(len(self.store.list_asset_revisions("case-outcome")), 2)
        with self.assertRaises(ValueError):
            self.store.record_generation_outcome("missing", "success")

    def test_generation_outcome_invalidates_empty_case_id(self):
        with self.assertRaises(ValueError):
            self.store.record_generation_outcome(" ", "success")

    def test_generation_outcome_rolls_back_case_and_revision_when_feedback_fails(self):
        record = {"id": "case-rollback", "name": "测试", "body": "v1", "status": "待验证"}
        self.store.save_data({"cases": [record], "modules": []})
        original_open = self.store._open
        class RollbackConnection:
            def __init__(self, inner):
                self.inner = inner
            def __enter__(self):
                self.inner.__enter__()
                return self
            def __exit__(self, *args):
                return self.inner.__exit__(*args)
            def execute(self, sql, params=()):
                if "INSERT INTO generation_feedback" in sql:
                    raise sqlite3.IntegrityError("simulated feedback failure")
                return self.inner.execute(sql, params)
            def __getattr__(self, name):
                return getattr(self.inner, name)
        with mock.patch.object(self.store, "_open", lambda: RollbackConnection(original_open())):
            with self.assertRaises(sqlite3.IntegrityError):
                self.store.record_generation_outcome("case-rollback", "success", "不应残留")
        saved = self.store.load_data()["cases"][0]
        self.assertEqual(saved["status"], "待验证")
        self.assertEqual(saved["generation_successes"], 0)
        self.assertEqual(saved["last_generation_notes"], "")
        self.assertEqual(len(self.store.list_asset_revisions("case-rollback")), 0)

    def test_retrieval_feedback_is_recorded_and_aggregated(self):
        feedback_id = self.store.record_retrieval_feedback(
            "红色夹克", "case-1", 0.82, "useful"
        )
        self.assertEqual(feedback_id, 1)
        analytics = self.store.get_retrieval_analytics()
        self.assertEqual(analytics["total"], 1)
        self.assertEqual(analytics["by_feedback"]["useful"], 1)
        self.assertEqual(analytics["top_cases"][0]["case_id"], "case-1")
        self.assertEqual(analytics["top_cases"][0]["useful"], 1)
        with self.assertRaises(ValueError):
            self.store.record_retrieval_feedback("", "case-1", 0.8, "useful")
        with self.assertRaises(ValueError):
            self.store.record_retrieval_feedback("q", "case-1", 0.8, "invalid")

    def test_deleted_assets_can_be_listed_and_restored(self):
        case = {"id": "case-deleted", "name": "删除案例", "body": "提示词", "tags": ["服装"]}
        self.store.save_data({"cases": [case], "modules": []})
        self.store.save_data({"cases": [], "modules": []})
        deleted = self.store.list_deleted_assets()
        self.assertEqual(len(deleted), 1)
        self.assertEqual(deleted[0]["asset_id"], "case-deleted")
        self.assertTrue(deleted[0]["can_restore"])
        restored = self.store.restore_asset_revision(
            "case-deleted", deleted[0]["revision_id"], expected_absent=True
        )
        self.assertEqual(restored["body"], "提示词")
        self.assertEqual(self.store.load_data()["cases"][0]["id"], "case-deleted")
        self.assertEqual(self.store.list_deleted_assets(), [])

    def test_deleted_restore_rejects_existing_asset_and_missing_revision(self):
        case = {"id": "case-existing", "name": "现存", "body": "v1"}
        self.store.save_data({"cases": [case], "modules": []})
        with self.assertRaises(StorageConflictError):
            self.store.restore_asset_revision("case-existing", "missing", expected_absent=True)
        self.store.save_data({"cases": [], "modules": []})
        revision_id = self.store.list_asset_revisions("case-existing")[0]["revision_id"]
        self.store.save_data({"cases": [case], "modules": []})
        with self.assertRaises(StorageConflictError):
            self.store.restore_asset_revision("case-existing", revision_id, expected_absent=True)
        with self.assertRaises(ValueError):
            self.store.restore_asset_revision("", " ")

    def test_serialize_operations_allows_nested_reentrant_lock(self):
        with self.store.operation():
            with self.store.operation():
                self.assertEqual(self.store.load_data()["cases"], [])
        self.assertFalse(self.store.is_exclusive_operation_active())

    def test_restore_waits_for_active_data_operation_and_new_writer_waits_for_restore(self):
        import threading
        import time
        other_root = self.root / "other"
        other_root.mkdir()
        other = Storage(other_root)
        other.save_data({"cases": [{"id": "restored", "body": "restored"}], "modules": []})
        archive = other.create_full_backup(other_root / "references", app_version="0.5.0")
        self.store.save_data({"cases": [{"id": "before", "body": "before"}], "modules": []})
        entered = threading.Event()
        release = threading.Event()
        started_restore = threading.Event()
        finished_restore = threading.Event()
        started_writer = threading.Event()
        finished_writer = threading.Event()
        def hold_operation():
            with self.store.operation():
                entered.set()
                release.wait(5)
        def restore_operation():
            started_restore.set()
            self.store.restore_full_backup(archive)
            finished_restore.set()
        def writer_operation():
            started_writer.set()
            self.store.save_data({"cases": [{"id": "after", "body": "after"}], "modules": []})
            finished_writer.set()
        holder = threading.Thread(target=hold_operation)
        holder.start()
        self.assertTrue(entered.wait(2))
        restorer = threading.Thread(target=restore_operation)
        restorer.start()
        self.assertTrue(started_restore.wait(2))
        time.sleep(0.2)
        self.assertFalse(finished_restore.is_set())
        release.set()
        self.assertTrue(finished_restore.wait(5))
        self.assertEqual(self.store.load_data()["cases"][0]["id"], "restored")
        holder.join(2)
        restorer.join(2)

    def test_restore_media_read_waits_until_database_swap_completes(self):
        import threading
        import time
        import ai_prompt_workbench as app
        self.store.save_data({"cases": [{"id": "case", "body": "body"}], "modules": []})
        image = self.root / "references"
        image.mkdir()
        (image / "one.png").write_bytes(b"image")
        archive = self.store.create_full_backup(image, app_version="0.5.0")
        original_replace = app.os.replace
        entered = threading.Event()
        release = threading.Event()
        media_done = threading.Event()
        def slow_replace(source, target):
            if "staging_references" in str(source) and str(target) == str(self.root / "references"):
                entered.set()
                release.wait(5)
            return original_replace(source, target)
        restore_thread = threading.Thread(target=self.store.restore_full_backup, args=(archive,))
        with mock.patch.object(app.os, "replace", side_effect=slow_replace):
            restore_thread.start()
            self.assertTrue(entered.wait(5))
            media = threading.Thread(target=lambda: (app._safe_reference_image_path("one.png"), media_done.set()))
            media.start()
            time.sleep(0.25)
            self.assertFalse(media_done.is_set())
            release.set()
            restore_thread.join(5)
            media.join(5)
        self.assertFalse(restore_thread.is_alive())
        self.assertTrue(media_done.is_set())

    def test_restore_directory_root_leases_are_isolated(self):
        import threading
        import time
        other_root = self.root / "other"
        other_root.mkdir()
        other = Storage(other_root)
        other.save_data({"cases": [{"id": "other", "body": "other"}], "modules": []})
        archive = other.create_full_backup(other_root / "references", app_version="0.5.0")
        self.store.save_data({"cases": [{"id": "own", "body": "own"}], "modules": []})
        started = threading.Event()
        release = threading.Event()
        other_done = threading.Event()
        original_open = self.store._open
        class SlowConnection:
            def __init__(self, inner):
                self.inner = inner
            def __enter__(self):
                self.inner.__enter__()
                return self.inner
            def __exit__(self, *args):
                release.wait(5)
                return self.inner.__exit__(*args)
            def __getattr__(self, name):
                return getattr(self.inner, name)
        def slow_open():
            return SlowConnection(original_open())
        with mock.patch.object(self.store, "_open", side_effect=slow_open):
            thread = threading.Thread(target=self.store.restore_full_backup, args=(archive,))
            thread.start()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not self.store.is_exclusive_operation_active():
                time.sleep(0.01)
            other_thread = threading.Thread(target=lambda: (other.save_data({"cases": [{"id": "other", "body": "other"}], "modules": []}), other_done.set()))
            other_thread.start()
            self.assertTrue(other_done.wait(2))
            release.set()
            thread.join(5)
            other_thread.join(2)
        self.assertFalse(thread.is_alive())

    def test_exclusive_restore_gate_releases_after_failure(self):
        self.store.save_data({"cases": [{"id": "keep", "body": "keep"}], "modules": []})
        with self.assertRaises(ValueError):
            self.store.restore_full_backup(self.root / "missing.zip")
        self.assertFalse(self.store.is_exclusive_operation_active())
        self.store.save_data({"cases": [{"id": "after", "body": "after"}], "modules": []})
        self.assertEqual(self.store.load_data()["cases"][0]["id"], "after")

    def test_safe_media_resolver_rejects_symlink_or_junction_root_and_file(self):
        import ai_prompt_workbench as app
        references = self.root / "references"
        references.mkdir()
        image = references / "one.png"
        image.write_bytes(b"png")
        with mock.patch.object(app, "HOME", self.root), mock.patch.object(app, "IMAGES", references):
            self.assertEqual(app._safe_reference_image_path("one.png"), image.resolve())
            self.assertIsNone(app._safe_reference_image_path("../one.png"))
            self.assertIsNone(app._safe_reference_image_path("one.txt"))
            outside = self.root / "outside.png"
            outside.write_bytes(b"outside")
            link = references / "link.png"
            try:
                os.symlink(outside, link)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation unavailable")
            self.assertIsNone(app._safe_reference_image_path("link.png"))

    def test_safe_media_resolver_rejects_symlink_root(self):
        import ai_prompt_workbench as app
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "one.png").write_bytes(b"png")
        link = self.root / "references"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        with mock.patch.object(app, "HOME", self.root), mock.patch.object(app, "IMAGES", link):
            self.assertIsNone(app._safe_reference_image_path("one.png"))

    def test_safe_media_resolver_rejects_junction_root_with_mock(self):
        import ai_prompt_workbench as app
        references = self.root / "references"
        references.mkdir()
        image = references / "one.png"
        image.write_bytes(b"png")
        with mock.patch.object(app, "HOME", self.root), mock.patch.object(app, "IMAGES", references):
            with mock.patch.object(app, "_is_link_or_junction", lambda path: Path(path) == references):
                self.assertIsNone(app._safe_reference_image_path("one.png"))

    def test_sanitize_backup_zip_name_rejects_traversal_and_long_names(self):
        self.assertEqual(sanitize_backup_zip_name("../outside.png"), "outside.png")
        self.assertEqual(sanitize_backup_zip_name("a/" + "x" * 300), "x" * 180)
        self.assertEqual(sanitize_backup_zip_name(""), "image.bin")

    def test_validate_restore_paths_rejects_absolute_windows_paths_and_ads(self):
        for name in ("C:/evil.png", "C:evil.png", "//server/share/evil.png", "/absolute.png", "references/a.txt:stream"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    validate_restore_paths([name])
        for name in ("references/CON.png", "references/a/PRN.txt", "references/clock$.png", "references/COM1.png"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    validate_restore_paths([name])
        for name in ("references/trailing.", "references/trailing ", "references/dir./file.png", "references/dir /file.png"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    validate_restore_paths([name])
        with self.assertRaises(ValueError):
            validate_restore_paths(["references/a.png", "references/A.PNG"])
        with self.assertRaises(ValueError):
            validate_restore_paths(["references/dir", "references/DIR/file.png"])
        self.assertTrue(is_windows_reserved_name("CON.txt"))
        self.assertTrue(is_windows_reserved_name("LPT9"))
        self.assertFalse(is_windows_reserved_name("CONSOLE.png"))

    def test_backup_rejects_symlink_reference_file(self):
        references = self.root / "references"
        references.mkdir()
        target = self.root / "target.png"
        target.write_bytes(b"target")
        try:
            os.symlink(target, references / "link.png")
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        with self.assertRaises(ValueError):
            self.store.create_full_backup(references, app_version="0.5.0")

    def test_backup_rejects_junction_reference_directory_with_mock(self):
        references = self.root / "references"
        references.mkdir()
        (references / "one.png").write_bytes(b"one")
        with mock.patch("storage._is_link_or_junction", lambda path: Path(path) == references):
            with self.assertRaisesRegex(ValueError, "符号链接或目录联接"):
                self.store.create_full_backup(references, app_version="0.5.0")

    def test_backup_snapshot_rolls_back_to_last_committed_save(self):
        self.store.save_data({"cases": [{"id": "keep", "body": "keep"}], "modules": []})
        try:
            conn = sqlite3.connect(self.root / "prompt_atelier.db")
            conn.execute("BEGIN EXCLUSIVE")
            conn.execute("INSERT INTO cases(asset_id,data) VALUES(?,?)", ("bad", '{"id":"bad","body":"bad"}'))
            archive = self.store.create_full_backup(self.root / "references", app_version="0.5.0")
            conn.rollback()
            conn.close()
        except Exception:
            if "conn" in locals():
                conn.close()
            raise
        restored_root = self.root / "restored"
        restored_root.mkdir()
        restored = Storage(restored_root)
        restored.restore_full_backup(archive)
        self.assertEqual([item["id"] for item in restored.load_data()["cases"]], ["keep"])

    def test_backup_skips_wal_sidecars_and_manifest_cannot_be_forged(self):
        self.store.save_data({"cases": [{"id": "keep", "body": "keep"}], "modules": []})
        (self.root / "prompt_atelier.db-wal").write_bytes(b"uncommitted wal")
        archive = self.store.create_full_backup(self.root / "references", app_version="0.5.0")
        with zipfile.ZipFile(archive) as zf:
            self.assertNotIn("prompt_atelier.db-wal", zf.namelist())
            manifest = json.loads(zf.read("backup_manifest.json"))
        self.assertEqual(manifest["files"]["prompt_atelier.db"]["size"], (self.root / "prompt_atelier.db").stat().st_size)

    def test_restore_rejects_manifest_declared_symlink_path(self):
        archive = self.root / "manifest-link.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("prompt_atelier.db", b"not-used")
            zf.writestr("backup_manifest.json", json.dumps({
                "format": "prompt_atelier_full_backup", "format_version": 1,
                "files": {"prompt_atelier.db": {"size": 8, "sha256": hashlib.sha256(b"not-used").hexdigest()},
                          "references/link.png": {"size": 0, "sha256": hashlib.sha256(b"").hexdigest()}},
            }))
            link = zipfile.ZipInfo("references/link.png")
            link.create_system = 3
            link.external_attr = 0o120777 << 16
            zf.writestr(link, b"")
        with self.assertRaisesRegex(ValueError, "符号链接"):
            self.store.restore_full_backup(archive)

    def test_restore_rejects_symlink_and_non_regular_files(self):
        for name, mode, content in (("references/link.png", 0o120777, b"target"), ("prompt_atelier.db", 0o10000, b"not-used")):
            archive = self.root / ("bad-" + name.replace("/", "-"))
            with zipfile.ZipFile(archive, "w") as zf:
                if name == "prompt_atelier.db":
                    zf.writestr(name, content)
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = mode << 16
                if name != "prompt_atelier.db":
                    zf.writestr(info, content)
                zf.writestr("backup_manifest.json", json.dumps({
                    "format": "prompt_atelier_full_backup", "format_version": 1,
                    "files": {"prompt_atelier.db": {"size": 8, "sha256": hashlib.sha256(b"not-used").hexdigest()}},
                }))
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    self.store.restore_full_backup(archive)

    def test_restore_requires_nonempty_manifest_and_exact_sizes(self):
        archive = self.root / "bad-manifest.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("prompt_atelier.db", b"db")
            zf.writestr("backup_manifest.json", json.dumps({
                "format": "prompt_atelier_full_backup", "format_version": 1,
                "files": {"prompt_atelier.db": {"size": 999, "sha256": hashlib.sha256(b"db").hexdigest()}},
            }))
        with self.assertRaisesRegex(ValueError, "大小"):
            self.store.restore_full_backup(archive)

    def test_restore_rejects_absolute_and_unexpected_manifest_paths(self):
        for path in ("../escape", "C:/escape", "unexpected/file.txt"):
            archive = self.root / ("bad-path-" + str(abs(hash(path))) + ".zip")
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("prompt_atelier.db", b"db")
                zf.writestr("backup_manifest.json", json.dumps({
                    "format": "prompt_atelier_full_backup", "format_version": 1,
                    "files": {"prompt_atelier.db": {"size": 2, "sha256": hashlib.sha256(b"db").hexdigest()},
                              path: {"size": 0, "sha256": hashlib.sha256(b"").hexdigest()}},
                }))
                if path != "unexpected/file.txt":
                    zf.writestr(path, b"")
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    self.store.restore_full_backup(archive)

    def test_restore_does_not_copy_legacy_json_directory(self):
        legacy = self.root / "legacy_json"
        legacy.mkdir()
        (legacy / "secret.json").write_text("{}", encoding="utf-8")
        archive = self.store.create_full_backup(self.root / "references", app_version="0.5.0")
        with zipfile.ZipFile(archive) as zf:
            self.assertFalse(any(name.startswith("legacy_json/") for name in zf.namelist()))

    def test_restore_failure_cleans_staging_and_preserves_original_home(self):
        self.store.save_data({"cases": [{"id": "original", "body": "original"}], "modules": []})
        archive = self.store.create_full_backup(self.root / "references", app_version="0.5.0")
        with mock.patch("storage.os.replace", side_effect=OSError("simulated move failure")):
            with self.assertRaises(OSError):
                self.store.restore_full_backup(archive)
        self.assertEqual(self.store.load_data()["cases"][0]["id"], "original")
        self.assertFalse(any(path.name.startswith("restore_") for path in self.root.iterdir()))

    def test_restore_cleanup_does_not_follow_symlink_staging_directory(self):
        target = self.root / "outside-target"
        target.mkdir()
        sentinel = target / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        staging = self.root / "restore_link"
        try:
            os.symlink(target, staging, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        with mock.patch("storage.tempfile.mkdtemp", return_value=str(staging)):
            with self.assertRaises(ValueError):
                self.store.restore_full_backup(self.root / "missing.zip")
        self.assertTrue(sentinel.exists())

    def test_restore_lock_is_released_when_cannot_acquire_exclusive_sqlite(self):
        archive = self.store.create_full_backup(self.root / "references", app_version="0.5.0")
        other = sqlite3.connect(self.root / "prompt_atelier.db")
        other.execute("BEGIN EXCLUSIVE")
        try:
            with self.assertRaises(sqlite3.OperationalError):
                self.store.restore_full_backup(archive)
        finally:
            other.rollback()
            other.close()
        self.assertFalse(self.store.is_exclusive_operation_active())
        self.store.save_data({"cases": [{"id": "after", "body": "after"}], "modules": []})

    def test_full_backup_is_usable_by_new_storage_instance(self):
        self.store.save_data({"cases": [{"id": "case", "name": "夹克", "body": "提示词"}], "modules": []})
        archive = self.store.create_full_backup(self.root / "references", app_version="0.5.0")
        other_root = self.root / "other"
        other_root.mkdir()
        other = Storage(other_root)
        other.restore_full_backup(archive)
        self.assertEqual(other.load_data()["cases"][0]["id"], "case")


class ApplicationValidationTests(unittest.TestCase):
    def test_http_host_and_content_type_policy(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ai_prompt_workbench.py").read_text(encoding="utf-8")
        self.assertIn("_validate_local_request", source)
        self.assertIn("application/json", source)
        self.assertIn("Sec-Fetch-Site", source)
        self.assertIn("拒绝跨站浏览器请求", source)
        self.assertIn("origin_ok", source)
        self.assertIn("STORAGE.operation()", source)

    def test_media_route_serves_only_registered_image_extensions(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ai_prompt_workbench.py").read_text(encoding="utf-8")
        self.assertIn('".png": "image/png"', source)
        self.assertIn("_safe_reference_image_path", source)
        self.assertIn("MEDIA_MIME_TYPES[media_file.suffix.lower()]", source)
        self.assertIn('"X-Content-Type-Options", "nosniff"', source)
        self.assertNotIn('"video/mp4"', source)
        self.assertNotIn('"text/plain"})', source[source.index("MEDIA_MIME_TYPES"):source.index("# Keep the legacy")])

    def test_request_size_bound_for_restore(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ai_prompt_workbench.py").read_text(encoding="utf-8")
        self.assertIn("500 * 1024 * 1024", source)
        self.assertIn("25 * 1024 * 1024", source)
        self.assertIn("application/zip", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
