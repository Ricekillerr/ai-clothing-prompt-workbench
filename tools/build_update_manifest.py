#!/usr/bin/env python3
"""Build or verify the Prompt Atelier update manifest from exact repository bytes."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_FILES = [
    "ai_prompt_workbench.py",
    "index.html",
    "启动AI服装提示词工作台.bat",
    "updater.py",
    "storage.py",
    "collector.py",
    "case_matcher.py",
    "ai_analyzer.py",
]


def build_manifest(notes: str = "") -> dict:
    app_source = (ROOT / "ai_prompt_workbench.py").read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION\\s*=\\s*["\']([^"\']+)["\']', app_source, re.MULTILINE)
    if not match:
        raise SystemExit("Cannot determine APP_VERSION from ai_prompt_workbench.py")
    version = match.group(1)
    missing = [name for name in RUNTIME_FILES if not (ROOT / name).is_file()]
    if missing:
        raise SystemExit("Missing runtime files: " + ", ".join(missing))
    current = {}
    manifest_path = ROOT / "update_manifest.json"
    if manifest_path.exists():
        try:
            current = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            current = {}
    return {
        "version": version,
        "notes": notes or current.get("notes") or f"Prompt Atelier V{version} update",
        "files": RUNTIME_FILES,
        "sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in RUNTIME_FILES
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if the checked-in manifest is stale")
    parser.add_argument("--notes", default="", help="release notes used when writing the manifest")
    args = parser.parse_args()
    expected = build_manifest(args.notes)
    path = ROOT / "update_manifest.json"
    if args.check:
        actual = json.loads(path.read_text(encoding="utf-8"))
        if actual != expected:
            print("ERROR: update_manifest.json is stale. Run: python tools/build_update_manifest.py")
            return 1
        print(f"OK: V{expected['version']} manifest matches all {len(RUNTIME_FILES)} runtime files.")
        return 0
    path.write_text(json.dumps(expected, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8", newline="\\n")
    print(f"Wrote update_manifest.json for V{expected['version']} ({len(RUNTIME_FILES)} files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
