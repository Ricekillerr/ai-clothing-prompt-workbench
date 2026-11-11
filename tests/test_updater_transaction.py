import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import updater


class UpdaterTransactionTests(unittest.TestCase):
    def prepare(self, root: Path, stage: Path):
        root.mkdir(parents=True, exist_ok=True)
        stage.mkdir(parents=True, exist_ok=True)
        old = {
            "ai_prompt_workbench.py": b"old application",
            "updater.py": b"old updater",
        }
        new = {
            "ai_prompt_workbench.py": b"new application",
            "updater.py": b"new updater",
        }
        hashes = {}
        for name, payload in old.items():
            (root / name).write_bytes(payload)
        for name, payload in new.items():
            (stage / name).write_bytes(payload)
            hashes[name] = hashlib.sha256(payload).hexdigest()
        (root / "installed_version.txt").write_text("0.5.2", encoding="utf-8")
        (stage / "update_meta.json").write_text(
            json.dumps({"version": "0.6.0", "files": hashes}), encoding="utf-8"
        )
        (root / "_update_backup").mkdir()
        (root / "_update_backup" / "keep.txt").write_text("previous backup", encoding="utf-8")
        return old, new

    def test_successful_install_preserves_previous_backup_and_creates_new_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "install"
            stage = Path(tmp) / "stage"
            old, new = self.prepare(root, stage)
            launcher = root / "启动AI服装提示词工作台.bat"
            launcher.write_text("@echo off\r\n", encoding="utf-8")
            argv = ["updater.py", str(stage), str(root), str(launcher)]
            with patch.object(updater.sys, "argv", argv), patch.object(
                updater, "service_running", return_value=False
            ), patch.object(updater, "launch_workbench") as launch:
                self.assertEqual(updater.main(), 0)
                launch.assert_called_once()
            for name, payload in new.items():
                self.assertEqual((root / name).read_bytes(), payload)
            self.assertEqual((root / "installed_version.txt").read_text(encoding="utf-8"), "0.6.0")
            self.assertEqual((root / "_update_backup" / "keep.txt").read_text(encoding="utf-8"), "previous backup")
            backups = list(root.glob("_update_backup_*"))
            self.assertEqual(len(backups), 1)
            for name, payload in old.items():
                self.assertEqual((backups[0] / name).read_bytes(), payload)

    def test_failed_mid_install_restores_every_replaced_file_and_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "install"
            stage = Path(tmp) / "stage"
            old, new = self.prepare(root, stage)
            launcher = root / "启动AI服装提示词工作台.bat"
            launcher.write_text("@echo off\r\n", encoding="utf-8")
            argv = ["updater.py", str(stage), str(root), str(launcher)]
            original_replace = updater.replace_with_retry

            def fail_once_for_staged_updater(src, dst):
                if Path(src) == stage / "updater.py":
                    raise OSError("simulated file lock")
                return original_replace(src, dst)

            with patch.object(updater.sys, "argv", argv), patch.object(
                updater, "service_running", return_value=False
            ), patch.object(updater, "replace_with_retry", side_effect=fail_once_for_staged_updater):
                self.assertEqual(updater.main(), 9)
            for name, payload in old.items():
                self.assertEqual((root / name).read_bytes(), payload)
            self.assertEqual((root / "installed_version.txt").read_text(encoding="utf-8"), "0.5.2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
