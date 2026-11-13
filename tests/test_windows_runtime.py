import hashlib
import importlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import tempfile
import threading
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest import mock
from urllib.request import Request, urlopen
from urllib.error import HTTPError


class WindowsUpdaterTransactionTests(unittest.TestCase):
    def _stage(self, root, files, version="0.6.6"):
        stage = root / "stage"
        stage.mkdir()
        expected = {}
        for name, value in files.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")
            expected[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        (stage / "update_meta.json").write_text(
            json.dumps({"version": version, "files": expected}), encoding="utf-8"
        )
        return stage

    def test_updater_log_rotates_and_redacts_local_paths_and_credentials(self):
        import updater

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "private-install-path"
            root.mkdir()
            with mock.patch.object(updater, "LOG_MAX_BYTES", 256), \
                 mock.patch.object(updater, "LOG_BACKUP_COUNT", 2):
                for _ in range(10):
                    updater.write_log(
                        root,
                        "failure at " + str(root)
                        + " Authorization: Bearer top-secret-token "
                        + "api_key=sk-12345678901234567890 " + "x" * 100,
                    )
            current = (root / "_update_error.log").read_text(encoding="utf-8")
            self.assertNotIn(str(root), current)
            self.assertNotIn("top-secret-token", current)
            self.assertNotIn("sk-12345678901234567890", current)
            self.assertTrue((root / "_update_error.log.1").is_file())
            self.assertTrue((root / "_update_error.log.2").is_file())
            self.assertFalse((root / "_update_error.log.3").exists())

    def test_update_installs_verified_files_and_retains_backup(self):
        import updater

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "install"
            root.mkdir()
            (root / "index.html").write_text("old-ui", encoding="utf-8")
            (root / "storage.py").write_text("old-storage", encoding="utf-8")
            launcher = root / "launcher.bat"
            launcher.write_text("@echo off", encoding="utf-8")
            stage = self._stage(root, {
                "index.html": "new-ui",
                "storage.py": "new-storage",
            })
            argv = ["updater.py", str(stage), str(root), str(launcher)]
            with mock.patch.object(sys, "argv", argv), \
                 mock.patch.object(updater, "service_running", return_value=False), \
                 mock.patch.object(updater, "launch_workbench") as launch:
                result = updater.main()
            self.assertEqual(result, 0)
            self.assertEqual((root / "index.html").read_text(encoding="utf-8"), "new-ui")
            self.assertEqual((root / "storage.py").read_text(encoding="utf-8"), "new-storage")
            self.assertEqual((root / "installed_version.txt").read_text(encoding="utf-8"), "0.6.6")
            self.assertEqual((root / "_update_backup" / "index.html").read_text(encoding="utf-8"), "old-ui")
            launch.assert_called_once()
            self.assertFalse(stage.exists())

    def test_in_place_update_preserves_user_database_references_and_local_settings(self):
        import updater

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "install"
            root.mkdir()
            (root / "index.html").write_text("old-ui", encoding="utf-8")
            (root / "storage.py").write_text("old-storage", encoding="utf-8")
            data_dir = root / "data"
            references = root / "references"
            data_dir.mkdir()
            references.mkdir()
            database = data_dir / "prompt_library.sqlite3"
            database.write_bytes(b"USER_DATABASE_MUST_REMAIN")
            reference = references / "garment-front.png"
            reference.write_bytes(b"USER_REFERENCE_MUST_REMAIN")
            local_settings = root / "local_settings.json"
            local_settings.write_text('{"private_local_setting":"keep"}', encoding="utf-8")
            launcher = root / "launcher.bat"
            launcher.write_text("@echo off", encoding="utf-8")
            stage = self._stage(root, {
                "index.html": "new-ui",
                "storage.py": "new-storage",
            })
            argv = ["updater.py", str(stage), str(root), str(launcher)]
            with mock.patch.object(sys, "argv", argv), \
                 mock.patch.object(updater, "service_running", return_value=False), \
                 mock.patch.object(updater, "launch_workbench"):
                result = updater.main()
            self.assertEqual(result, 0)
            self.assertEqual((root / "index.html").read_text(encoding="utf-8"), "new-ui")
            self.assertEqual(database.read_bytes(), b"USER_DATABASE_MUST_REMAIN")
            self.assertEqual(reference.read_bytes(), b"USER_REFERENCE_MUST_REMAIN")
            self.assertEqual(json.loads(local_settings.read_text(encoding="utf-8")),
                             {"private_local_setting": "keep"})
            self.assertFalse(stage.exists())

    def test_clean_install_creates_program_files_without_touching_user_store(self):
        import updater

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "install"
            root.mkdir()
            user_store = root / "user_store"
            user_store.mkdir()
            (user_store / "sentinel.txt").write_text("keep", encoding="utf-8")
            launcher = root / "launcher.bat"
            launcher.write_text("@echo off", encoding="utf-8")
            stage = self._stage(root, {
                "index.html": "first-ui",
                "storage.py": "first-storage",
            }, version="1.0.0")
            argv = ["updater.py", str(stage), str(root), str(launcher)]
            with mock.patch.object(sys, "argv", argv), \
                 mock.patch.object(updater, "service_running", return_value=False), \
                 mock.patch.object(updater, "launch_workbench") as launch:
                result = updater.main()
            self.assertEqual(result, 0)
            self.assertEqual((root / "index.html").read_text(encoding="utf-8"), "first-ui")
            self.assertEqual((root / "storage.py").read_text(encoding="utf-8"), "first-storage")
            self.assertEqual((user_store / "sentinel.txt").read_text(encoding="utf-8"), "keep")
            self.assertEqual((root / "installed_version.txt").read_text(encoding="utf-8"), "1.0.0")
            launch.assert_called_once()
            self.assertFalse(stage.exists())

    def test_rollback_failure_is_reported_and_original_backup_is_retained(self):
        import updater

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "install"
            root.mkdir()
            (root / "index.html").write_text("old-ui", encoding="utf-8")
            (root / "storage.py").write_text("old-storage", encoding="utf-8")
            launcher = root / "launcher.bat"
            launcher.write_text("@echo off", encoding="utf-8")
            stage = self._stage(root, {
                "index.html": "new-ui",
                "storage.py": "new-storage",
            })
            real_replace = updater.replace_with_retry
            calls = {"count": 0}

            def fail_install_and_rollback(src, dst):
                calls["count"] += 1
                if calls["count"] in {2, 4}:
                    raise OSError("injected install/rollback failure")
                return real_replace(src, dst)

            argv = ["updater.py", str(stage), str(root), str(launcher)]
            with mock.patch.object(sys, "argv", argv), \
                 mock.patch.object(updater, "service_running", return_value=False), \
                 mock.patch.object(updater, "launch_workbench") as launch, \
                 mock.patch.object(updater, "replace_with_retry", side_effect=fail_install_and_rollback):
                result = updater.main()

            self.assertEqual(result, 9)
            self.assertEqual((root / "index.html").read_text(encoding="utf-8"), "new-ui")
            self.assertEqual((root / "storage.py").read_text(encoding="utf-8"), "old-storage")
            backups = list(root.glob("prompt_atelier_backup_*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / "index.html").read_text(encoding="utf-8"), "old-ui")
            log = (root / "_update_error.log").read_text(encoding="utf-8")
            self.assertIn("自动回滚未完整完成", log)
            self.assertIn(backups[0].name.casefold(), log.casefold())
            self.assertFalse(stage.exists())
            launch.assert_not_called()

    def test_mid_transaction_failure_restores_every_original_file(self):
        import updater

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "install"
            root.mkdir()
            originals = {"index.html": "old-ui", "storage.py": "old-storage"}
            for name, value in originals.items():
                (root / name).write_text(value, encoding="utf-8")
            launcher = root / "launcher.bat"
            launcher.write_text("@echo off", encoding="utf-8")
            stage = self._stage(root, {
                "index.html": "new-ui",
                "storage.py": "new-storage",
            })
            real_replace = updater.replace_with_retry
            calls = {"count": 0}

            def fail_once(src, dst):
                calls["count"] += 1
                if calls["count"] == 2:
                    raise OSError("injected replacement failure")
                return real_replace(src, dst)

            argv = ["updater.py", str(stage), str(root), str(launcher)]
            with mock.patch.object(sys, "argv", argv), \
                 mock.patch.object(updater, "service_running", return_value=False), \
                 mock.patch.object(updater, "launch_workbench") as launch, \
                 mock.patch.object(updater, "replace_with_retry", side_effect=fail_once):
                result = updater.main()
            self.assertEqual(result, 9)
            for name, value in originals.items():
                self.assertEqual((root / name).read_text(encoding="utf-8"), value)
            self.assertFalse((root / "installed_version.txt").exists())
            self.assertTrue((root / "_update_error.log").is_file())
            self.assertFalse(list(root.glob("*.update_tmp")))
            self.assertFalse(list(root.glob("*.restore_tmp")))
            launch.assert_not_called()
            self.assertFalse(stage.exists())


class WindowsLocalServerSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.old_userprofile = os.environ.get("USERPROFILE")
        cls.old_home = os.environ.get("HOME")
        os.environ["USERPROFILE"] = cls.temp.name
        os.environ["HOME"] = cls.temp.name
        sys.modules.pop("ai_prompt_workbench", None)
        cls.app = importlib.import_module("ai_prompt_workbench")
        cls.home = Path(cls.temp.name) / "PromptAtelierWindowsSmoke"
        cls.images = cls.home / "references"
        cls.images.mkdir(parents=True, exist_ok=True)
        cls.app.HOME = cls.home
        cls.app.IMAGES = cls.images
        cls.app.STORAGE = cls.app.Storage(cls.home, legacy_json=cls.home / "prompt_library.json")

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("ai_prompt_workbench", None)
        if cls.old_userprofile is None:
            os.environ.pop("USERPROFILE", None)
        else:
            os.environ["USERPROFILE"] = cls.old_userprofile
        if cls.old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = cls.old_home
        cls.temp.cleanup()

    def _start_server(self):
        server = self.app.ThreadingHTTPServer(("127.0.0.1", 0), self.app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread, f"http://127.0.0.1:{server.server_address[1]}"

    def test_app_logs_rotate_without_recording_user_paths_or_exception_text(self):
        previous_limit = self.app.LOG_MAX_BYTES
        previous_backups = self.app.LOG_BACKUP_COUNT
        self.app.LOG_MAX_BYTES = 256
        self.app.LOG_BACKUP_COUNT = 2
        try:
            secret_path = str(self.home / "private-client-folder")
            secret_error = RuntimeError("sensitive exception at " + secret_path + " sk-very-secret-token")
            for _ in range(10):
                self.app.log_background_error(secret_error, "source-token")
                self.app.log_api_error(
                    "/api/ai/analyze?prompt_path=" + secret_path,
                    secret_error,
                )
            for name in ("background_tasks.log", "api_errors.log"):
                current = (self.home / name).read_text(encoding="utf-8")
                self.assertNotIn(secret_path, current)
                self.assertNotIn("very-secret-token", current)
                self.assertTrue((self.home / (name + ".1")).is_file())
                self.assertTrue((self.home / (name + ".2")).is_file())
                self.assertFalse((self.home / (name + ".3")).exists())
        finally:
            self.app.LOG_MAX_BYTES = previous_limit
            self.app.LOG_BACKUP_COUNT = previous_backups

    def test_scans_for_the_same_source_are_serialized(self):
        source_dir = self.home / "history"
        source_dir.mkdir()
        (source_dir / "prompt.txt").write_text("prompt", encoding="utf-8")
        source_id = self.app.STORAGE.register_scan_source(str(source_dir))
        original_scan = self.app.scan_folder
        state_lock = threading.Lock()
        active = 0
        maximum_active = 0
        errors = []
        start = threading.Barrier(2)

        def delayed_scan(folder, recursive=True, extensions=None, previous=None, force_hash=False):
            nonlocal active, maximum_active
            with state_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                time.sleep(0.15)
                return original_scan(
                    folder, recursive, extensions,
                    previous=previous, force_hash=force_hash,
                )
            finally:
                with state_lock:
                    active -= 1

        def run_scan():
            try:
                start.wait(timeout=5)
                self.app.scan_registered_source(source_id, force_hash=True)
            except Exception as exc:
                errors.append(exc)

        try:
            with mock.patch.object(self.app, "scan_folder", side_effect=delayed_scan):
                workers = [
                    threading.Thread(target=run_scan, daemon=True),
                    threading.Thread(target=run_scan, daemon=True),
                ]
                for worker in workers:
                    worker.start()
                for worker in workers:
                    worker.join(timeout=10)
                self.assertTrue(all(not worker.is_alive() for worker in workers))
            self.assertEqual(errors, [])
            self.assertEqual(maximum_active, 1)
        finally:
            pass

    def test_heartbeat_endpoint_remains_responsive_during_exclusive_restore(self):
        server, thread, base = self._start_server()
        finished = threading.Event()
        errors = []

        def send_heartbeat():
            try:
                request = Request(
                    base + "/api/client/heartbeat",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=5) as response:
                    self.assertTrue(json.loads(response.read().decode("utf-8"))["ok"])
            except Exception as exc:
                errors.append(exc)
            finally:
                finished.set()

        try:
            with self.app.STORAGE.exclusive_operation():
                worker = threading.Thread(target=send_heartbeat, daemon=True)
                worker.start()
                self.assertTrue(
                    finished.wait(2),
                    "heartbeat must not wait behind the exclusive restore gate",
                )
            worker.join(timeout=5)
            self.assertEqual(errors, [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_heartbeat_watchdog_defers_shutdown_until_exclusive_restore_finishes(self):
        stop_event = self.app.AUTO_SCAN_STOP
        was_stopped = stop_event.is_set()
        previous_last = self.app.LAST_CLIENT_HEARTBEAT
        previous_close = self.app.CLOSE_REQUESTED_AT
        stop_event.clear()
        self.app.LAST_CLIENT_HEARTBEAT = time.monotonic() - 30
        self.app.CLOSE_REQUESTED_AT = 0.0
        shutdown_called = threading.Event()

        class FakeServer:
            def shutdown(self):
                shutdown_called.set()

        worker = threading.Thread(
            target=self.app.heartbeat_watchdog,
            args=(FakeServer(),),
            daemon=True,
        )
        try:
            with self.app.STORAGE.exclusive_operation():
                worker.start()
                time.sleep(1.25)
                self.assertFalse(
                    shutdown_called.is_set(),
                    "watchdog must not stop the process during exclusive restore",
                )
            self.assertTrue(
                shutdown_called.wait(2.5),
                "watchdog should resume normal idle shutdown after restore completes",
            )
        finally:
            stop_event.set()
            worker.join(timeout=3)
            self.app.LAST_CLIENT_HEARTBEAT = previous_last
            self.app.CLOSE_REQUESTED_AT = previous_close
            if not was_stopped:
                stop_event.clear()

    def test_case_and_module_crud_api_round_trip_with_revisions_and_recycle_restore(self):
        original = self.app.STORAGE.load_data()
        case_id = "api-crud-case-" + __import__("uuid").uuid4().hex
        module_id = "api-crud-module-" + __import__("uuid").uuid4().hex
        server, thread, base = self._start_server()

        def request_json(path, payload=None, method=None):
            body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request = Request(
                base + path,
                data=body,
                headers={"Content-Type": "application/json"} if body is not None else {},
                method=method or ("POST" if body is not None else "GET"),
            )
            with urlopen(request, timeout=5) as response:
                return json.loads(response.read().decode("utf-8"))

        try:
            created_case = {
                "id": case_id, "name": "CRUD API 原始案例", "body": "original prompt",
                "status": "待验证", "created_at": "2026-01-01T00:00:00Z",
            }
            created_module = {
                "id": module_id, "name": "CRUD API 原始模块", "body": "original module",
                "module_type": "面料", "created_at": "2026-01-01T00:00:00Z",
            }
            request_json("/api/data", {
                "cases": original["cases"] + [created_case],
                "modules": original["modules"] + [created_module],
            })
            edited_case = dict(created_case, name="CRUD API 修改案例", body="edited prompt")
            edited_module = dict(created_module, name="CRUD API 修改模块", body="edited module")
            request_json("/api/data", {
                "cases": original["cases"] + [edited_case],
                "modules": original["modules"] + [edited_module],
            })
            case_history = request_json("/api/assets/revisions?asset_id=" + case_id)["revisions"]
            module_history = request_json("/api/assets/revisions?asset_id=" + module_id)["revisions"]
            self.assertEqual(case_history[0]["reason"], "edited")
            self.assertEqual(module_history[0]["reason"], "edited")
            self.assertEqual(
                request_json("/api/assets/revision?asset_id=" + case_id + "&revision_id=" + case_history[0]["revision_id"])["asset"]["body"],
                "original prompt",
            )

            request_json("/api/data", {"cases": original["cases"], "modules": original["modules"]})
            deleted = request_json("/api/assets/deleted")["items"]
            deleted_by_id = {item["asset_id"]: item for item in deleted}
            self.assertIn(case_id, deleted_by_id)
            self.assertIn(module_id, deleted_by_id)
            for asset_id in (case_id, module_id):
                item = deleted_by_id[asset_id]
                result = request_json("/api/assets/revisions/restore", {
                    "asset_id": asset_id,
                    "revision_id": item["revision_id"],
                    "expected_absent": True,
                })
                self.assertTrue(result["ok"])
                self.assertIn(asset_id, {
                    asset["id"] for key in ("cases", "modules") for asset in result["data"][key]
                })
        finally:
            try:
                request_json("/api/data", original)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_full_backup_download_and_restore_http_round_trip(self):
        original = self.app.STORAGE.load_data()
        case_id = "api-backup-case-" + __import__("uuid").uuid4().hex
        image_name = "api-backup-" + __import__("uuid").uuid4().hex + ".png"
        image_path = self.images / image_name
        server, thread, base = self._start_server()

        def post_json(payload):
            request = Request(
                base + "/api/data",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))

        try:
            image_path.write_bytes(b"original-reference-image")
            expected_case = {
                "id": case_id, "name": "完整备份 API 测试", "body": "original backup prompt",
                "status": "已验证成功",
            }
            post_json({
                "cases": original["cases"] + [expected_case],
                "modules": original["modules"],
            })
            with urlopen(base + "/api/backup/full", timeout=20) as response:
                self.assertEqual(response.headers.get_content_type(), "application/zip")
                archive_bytes = response.read()
            self.assertGreater(len(archive_bytes), 0)

            post_json({
                "cases": [item for item in original["cases"] if item.get("id") != case_id],
                "modules": original["modules"],
            })
            image_path.write_bytes(b"changed-reference-image")
            restore_request = Request(
                base + "/api/backup/restore",
                data=archive_bytes,
                headers={"Content-Type": "application/zip"},
                method="POST",
            )
            with urlopen(restore_request, timeout=30) as response:
                restored = json.loads(response.read().decode("utf-8"))
            self.assertTrue(restored.get("ok"))
            current = self.app.STORAGE.load_data()
            case = next(item for item in current["cases"] if item["id"] == case_id)
            self.assertEqual(case["body"], "original backup prompt")
            self.assertEqual(image_path.read_bytes(), b"original-reference-image")
        finally:
            try:
                post_json(original)
                if image_path.exists():
                    image_path.unlink()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_history_ingestion_review_and_ai_approval_http_round_trip(self):
        original = self.app.STORAGE.load_data()
        source_dir = self.home / ("history-api-" + __import__("uuid").uuid4().hex)
        source_dir.mkdir(parents=True)
        prompt_path = source_dir / "successful_prompt.txt"
        image_path = source_dir / "jacket_front.png"
        prompt_text = "以参考视频锁定人物与镜头，只替换深黑色夹克。\n保留原始动作和背景。"
        prompt_path.write_text(prompt_text, encoding="utf-8")
        image_path.write_bytes(b"mock-image-bytes-for-ingestion")
        server, thread, base = self._start_server()

        def request_json(path, payload=None):
            body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request = Request(
                base + path,
                data=body,
                headers={"Content-Type": "application/json"} if body is not None else {},
                method="POST" if body is not None else "GET",
            )
            with urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))

        try:
            source = request_json("/api/scan/source", {"folder": str(source_dir), "recursive": True})
            scan = request_json("/api/scan/run", {"source_id": source["source_id"]})
            self.assertEqual(scan["summary"]["added"], 2)
            pending = request_json("/api/scan/pending")["items"]
            by_name = {item["name"]: item for item in pending}
            self.assertIn(prompt_path.name, by_name)
            self.assertIn(image_path.name, by_name)

            request_json("/api/scan/review", {"path": str(prompt_path), "status": "approved"})
            pending_after_review = request_json("/api/scan/pending")["items"]
            self.assertNotIn(str(prompt_path), {item["path"] for item in pending_after_review})

            def fake_analysis(prompt_file, asset_files, api_key, endpoint, model,
                              expected_prompt_sha256=None, expected_asset_sha256s=None):
                self.assertEqual(Path(prompt_file).read_text(encoding="utf-8"), prompt_text)
                self.assertEqual(len(asset_files), 1)
                return {
                    "title": "HTTP 采集测试夹克",
                    "category": "上装",
                    "subcategory": "夹克",
                    "prompt_text": prompt_text,
                    "prompt_summary": "保留人物与镜头，只替换夹克",
                    "asset_paths": list(asset_files),
                    "asset_roles": [{
                        "asset_index": 1, "asset_path": asset_files[0],
                        "role": "front", "role_label": "正面整体图",
                        "priority": "primary", "governs": ["整体结构"],
                    }],
                    "outfit_items": [{
                        "item_index": 1, "category": "上装", "name": "深黑夹克",
                        "color": "深黑", "material": "尼龙", "confidence": 0.9,
                    }],
                    "outfit_relations": [],
                }

            with mock.patch.object(self.app, "analyze_assets", side_effect=fake_analysis):
                analysis = request_json("/api/ai/analyze", {
                    "prompt_path": str(prompt_path),
                    "asset_paths": [str(image_path)],
                    "api_key": "test-key-not-persisted",
                    "endpoint": "https://api.example.invalid/v1",
                    "model": "test-model",
                })
            self.assertTrue(analysis["ok"])
            self.assertEqual(analysis["result"]["prompt_text"], prompt_text)
            approved = request_json("/api/ai/approve", {"analysis_id": analysis["analysis_id"]})
            self.assertTrue(approved["ok"])
            imported = next(item for item in approved["data"]["cases"] if item["id"] == approved["asset_id"])
            self.assertEqual(imported["body"], prompt_text)
            self.assertEqual(imported["name"], "HTTP 采集测试夹克")
            self.assertEqual(imported["image_roles"][0]["priority"], "primary")
            self.assertEqual(imported["outfit_items"][0]["name"], "深黑夹克")
            self.assertEqual(imported["provenance"]["source_prompt"]["path"], str(prompt_path))
            copied_image = self.images / imported["image"]
            self.assertTrue(copied_image.is_file())
            self.assertEqual(copied_image.read_bytes(), image_path.read_bytes())
            self.assertEqual(request_json("/api/ai/reviews")["reviews"], [])
        finally:
            try:
                self.app.STORAGE.save_data(original)
                for copied in self.images.glob("history_*"):
                    copied.unlink(missing_ok=True)
            finally:
                shutil.rmtree(source_dir, ignore_errors=True)
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_recycle_bin_api_exposes_deleted_assets_for_recovery(self):
        self.app.STORAGE.save_data({
            "cases": [{
                "id": "api-recycle-001",
                "name": "API 回收站测试",
                "body": "recoverable",
                "status": "已验证成功",
            }],
            "modules": [],
        })
        self.app.STORAGE.save_data({"cases": [], "modules": []})
        server, thread, base = self._start_server()
        try:
            request = Request(
                base + "/api/assets/deleted",
                headers={"Host": f"127.0.0.1:{server.server_address[1]}"},
            )
            with urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(payload["items"][0]["asset_id"], "api-recycle-001")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_media_endpoint_never_serves_html_as_same_origin_content(self):
        server, thread, base = self._start_server()
        try:
            (self.images / "payload.html").write_text(
                "<script>window.__promptAtelierInjected=true</script>",
                encoding="utf-8",
            )
            request = Request(
                base + "/media/payload.html",
                headers={"Host": f"127.0.0.1:{server.server_address[1]}"},
            )
            with self.assertRaises(HTTPError) as rejected:
                urlopen(request, timeout=5)
            self.assertEqual(rejected.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_media_endpoint_rejects_symlinked_image_files(self):
        server, thread, base = self._start_server()
        try:
            outside = self.home / "private-outside.png"
            outside.write_bytes(b"private")
            linked = self.images / "linked.png"
            try:
                linked.symlink_to(outside)
            except OSError:
                self.skipTest("当前系统未授予创建符号链接的权限")
            request = Request(
                base + "/media/linked.png",
                headers={"Host": f"127.0.0.1:{server.server_address[1]}"},
            )
            with self.assertRaises(HTTPError) as rejected:
                urlopen(request, timeout=5)
            self.assertEqual(rejected.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_loopback_api_rejects_dns_rebinding_host_header(self):
        server, thread, base = self._start_server()
        try:
            port = server.server_address[1]
            request = Request(
                base + "/api/data",
                headers={"Host": f"attacker.example:{port}"},
            )
            with self.assertRaises(HTTPError) as rejected:
                urlopen(request, timeout=5)
            self.assertEqual(rejected.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_loopback_api_rejects_cross_site_backup_requests(self):
        server, thread, base = self._start_server()
        try:
            port = server.server_address[1]
            backup_dir = self.home / "backups"
            before = set(backup_dir.glob("PromptAtelier_full_backup_*.zip")) if backup_dir.exists() else set()
            request = Request(
                base + "/api/backup/full",
                headers={
                    "Host": f"127.0.0.1:{port}",
                    "Sec-Fetch-Site": "cross-site",
                },
            )
            with self.assertRaises(HTTPError) as rejected:
                urlopen(request, timeout=5)
            self.assertEqual(rejected.exception.code, 403)
            after = set(backup_dir.glob("PromptAtelier_full_backup_*.zip")) if backup_dir.exists() else set()
            self.assertEqual(after, before)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_loopback_api_rejects_cross_origin_json_writes(self):
        server, thread, base = self._start_server()
        try:
            port = server.server_address[1]
            before = self.app.STORAGE.load_data()
            request = Request(
                base + "/api/data",
                data=json.dumps({"cases": [{"id": "csrf-injected"}], "modules": []}).encode("utf-8"),
                headers={
                    "Host": f"127.0.0.1:{port}",
                    "Origin": "https://attacker.example",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with self.assertRaises(HTTPError) as rejected:
                urlopen(request, timeout=5)
            self.assertEqual(rejected.exception.code, 403)
            self.assertEqual(self.app.STORAGE.load_data(), before)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_loopback_api_rejects_simple_form_content_type(self):
        server, thread, base = self._start_server()
        try:
            port = server.server_address[1]
            before = self.app.STORAGE.load_data()
            request = Request(
                base + "/api/data",
                data=json.dumps({"cases": [{"id": "form-csrf-injected"}], "modules": []}).encode("utf-8"),
                headers={
                    "Host": f"127.0.0.1:{port}",
                    "Origin": f"http://127.0.0.1:{port}",
                    "Content-Type": "text/plain",
                },
                method="POST",
            )
            with self.assertRaises(HTTPError) as rejected:
                urlopen(request, timeout=5)
            self.assertEqual(rejected.exception.code, 415)
            self.assertEqual(self.app.STORAGE.load_data(), before)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    @unittest.skipUnless(os.name == "nt", "actual .bat launcher test requires Windows")
    def test_real_windows_launcher_starts_and_closes_service(self):
        repo_root = Path(__file__).resolve().parents[1]
        launcher = repo_root / "启动AI服装提示词工作台.bat"
        self.assertTrue(launcher.is_file())
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "profile"
            home.mkdir()
            site = Path(tmp) / "site"
            site.mkdir()
            (site / "sitecustomize.py").write_text(
                "import webbrowser\\nwebbrowser.open = lambda *args, **kwargs: False\\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["USERPROFILE"] = str(home)
            env["HOME"] = str(home)
            env["PYTHONPATH"] = str(site) + os.pathsep + env.get("PYTHONPATH", "")
            command = f'cmd.exe /c "{launcher}"'
            process = subprocess.Popen(
                command, cwd=str(repo_root), env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            connected = False
            try:
                deadline = time.time() + 25
                while time.time() < deadline:
                    if process.poll() is not None:
                        break
                    probe = socket.socket()
                    probe.settimeout(0.2)
                    try:
                        if probe.connect_ex(("127.0.0.1", 8765)) == 0:
                            connected = True
                            break
                    finally:
                        probe.close()
                    time.sleep(0.25)
                self.assertTrue(connected, "BAT launcher did not start the local HTTP service")
                with urlopen("http://127.0.0.1:8765/api/version", timeout=10) as response:
                    version = json.loads(response.read().decode("utf-8"))
                self.assertEqual(version["version"], self.app.APP_VERSION)
                request = Request(
                    "http://127.0.0.1:8765/api/client/heartbeat",
                    data=b"{}", headers={"Content-Type": "application/json"}, method="POST",
                )
                with urlopen(request, timeout=10) as response:
                    self.assertTrue(json.loads(response.read().decode("utf-8"))["ok"])
                close_request = Request(
                    "http://127.0.0.1:8765/api/client/close",
                    data=b"{}", headers={"Content-Type": "application/json"}, method="POST",
                )
                with urlopen(close_request, timeout=10) as response:
                    self.assertTrue(json.loads(response.read().decode("utf-8"))["ok"])
                process.wait(timeout=12)
                output = process.stdout.read().decode("utf-8", errors="replace") if process.stdout else ""
                self.assertEqual(process.returncode, 0, output)
                deadline = time.time() + 5
                while time.time() < deadline:
                    probe = socket.socket()
                    probe.settimeout(0.2)
                    try:
                        if probe.connect_ex(("127.0.0.1", 8765)) != 0:
                            break
                    finally:
                        probe.close()
                    time.sleep(0.2)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                if process.stdout:
                    process.stdout.close()

    def test_local_api_backup_and_restart_preserve_user_data(self):
        server, thread, base = self._start_server()
        try:
            with urlopen(base + "/api/version", timeout=10) as response:
                version = json.loads(response.read().decode("utf-8"))
            self.assertEqual(version["version"], self.app.APP_VERSION)

            with urlopen(base + "/api/update/auto", timeout=10) as response:
                self.assertFalse(json.loads(response.read().decode("utf-8"))["enabled"])
            with urlopen(base + "/api/settings/auto-update", timeout=10) as response:
                self.assertFalse(json.loads(response.read().decode("utf-8"))["enabled"])
            with urlopen(base + "/api/settings/auto-update", timeout=10) as response:
                self.assertFalse(json.loads(response.read().decode("utf-8"))["enabled"])
            enable_request = Request(
                base + "/api/update/auto",
                data=json.dumps({"enabled": True}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(enable_request, timeout=10) as response:
                self.assertTrue(json.loads(response.read().decode("utf-8"))["enabled"])
            with urlopen(base + "/api/update/auto", timeout=10) as response:
                self.assertTrue(json.loads(response.read().decode("utf-8"))["enabled"])
            with urlopen(base + "/api/settings/auto-update", timeout=10) as response:
                self.assertTrue(json.loads(response.read().decode("utf-8"))["enabled"])
            disable_request = Request(
                base + "/api/update/auto",
                data=json.dumps({"enabled": False}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(disable_request, timeout=10) as response:
                self.assertFalse(json.loads(response.read().decode("utf-8"))["enabled"])
            skip_request = Request(
                base + "/api/update/auto/skip",
                data=json.dumps({"version": "9.9.9"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(skip_request, timeout=10) as response:
                self.assertEqual(json.loads(response.read().decode("utf-8"))["skipped_version"], "9.9.9")
            with urlopen(base + "/api/update/auto", timeout=10) as response:
                self.assertEqual(json.loads(response.read().decode("utf-8"))["skipped_version"], "9.9.9")
            clear_skip = Request(
                base + "/api/update/auto/skip",
                data=json.dumps({"version": ""}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(clear_skip, timeout=10) as response:
                self.assertEqual(json.loads(response.read().decode("utf-8"))["skipped_version"], "")

            payload = {
                "cases": [{"id": "windows-smoke-case", "name": "Windows smoke",
                           "body": "verified prompt", "status": "已验证成功"}],
                "modules": [],
            }
            request = Request(
                base + "/api/data",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=10) as response:
                self.assertTrue(json.loads(response.read().decode("utf-8"))["ok"])

            (self.images / "smoke-reference.png").write_bytes(b"smoke-reference-bytes")
            with urlopen(base + "/api/backup/full", timeout=20) as response:
                archive_bytes = response.read()
            with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
                names = set(archive.namelist())
                self.assertIn("database/prompt_library.sqlite3", names)
                self.assertIn("data/prompt_library.json", names)
                self.assertIn("references/smoke-reference.png", names)
                self.assertIn("backup_manifest.json", names)
                self.assertTrue(archive.testzip() is None)

            changed_payload = {
                "cases": [{"id": "after-restore", "name": "After restore", "body": "temporary"}],
                "modules": [],
            }
            changed_request = Request(
                base + "/api/data",
                data=json.dumps(changed_payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(changed_request, timeout=10) as response:
                self.assertTrue(json.loads(response.read().decode("utf-8"))["ok"])
            (self.images / "after-restore.png").write_bytes(b"temporary-reference")
            restore_request = Request(
                base + "/api/backup/restore",
                data=archive_bytes,
                headers={"Content-Type": "application/zip"},
                method="POST",
            )
            with urlopen(restore_request, timeout=30) as response:
                restored_result = json.loads(response.read().decode("utf-8"))
            self.assertTrue(restored_result["ok"])
            self.assertEqual(restored_result["cases"], 1)
            self.assertTrue((self.home / "backups" / restored_result["safety_backup"]).is_file())
            self.assertFalse((self.images / "after-restore.png").exists())

            # A corrupt upload must be rejected without changing restored data.
            corrupt_request = Request(
                base + "/api/backup/restore",
                data=b"not-a-valid-zip-archive",
                headers={"Content-Type": "application/zip"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as rejected:
                urlopen(corrupt_request, timeout=20)
            self.assertEqual(rejected.exception.code, 400)
            with urlopen(base + "/api/data", timeout=10) as response:
                unchanged = json.loads(response.read().decode("utf-8"))
            self.assertEqual(unchanged["cases"][0]["id"], "windows-smoke-case")
            self.assertTrue((self.images / "smoke-reference.png").is_file())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        restarted, restarted_thread, restarted_base = self._start_server()
        try:
            with urlopen(restarted_base + "/api/data", timeout=10) as response:
                restored = json.loads(response.read().decode("utf-8"))
            self.assertEqual(restored["cases"][0]["id"], "windows-smoke-case")
            self.assertEqual(restored["cases"][0]["body"], "verified prompt")
            self.assertTrue((self.images / "smoke-reference.png").is_file())
        finally:
            restarted.shutdown()
            restarted.server_close()
            restarted_thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
