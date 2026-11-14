# -*- coding: utf-8 -*-
"""Generate update_manifest.json from the exact local release files."""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAIN = ROOT / "ai_prompt_workbench.py"
RELEASE_FILES = [
    "ai_prompt_workbench.py",
    "index.html",
    "updater.py",
    "启动AI服装提示词工作台.bat",
    "storage.py",
    "ai_analyzer.py",
    "collector.py",
    "case_matcher.py",
    "使用说明_Prompt_Atelier_V1.0_候选版.txt",
]

def sha256(path):
    # Text checkouts may use CRLF on Windows. BAT files are intentionally
    # stored as CRLF with -text so raw downloads and updates remain executable.
    content = path.read_bytes()
    normalized = content if path.suffix.lower() == ".bat" else content.replace(b"\r\n", b"\n")
    return hashlib.sha256(normalized).hexdigest()

def main():
    source = MAIN.read_text(encoding="utf-8-sig")
    match = re.search(r"""^\s*APP_VERSION\s*=\s*["']([^"']+)["']""", source, re.M)
    if not match:
        raise SystemExit("错误：未能从主程序读取 APP_VERSION。")
    version = match.group(1)
    missing = [name for name in RELEASE_FILES if not (ROOT / name).is_file()]
    if missing:
        raise SystemExit("错误：发布文件缺失：" + "、".join(missing))
    manifest_path = ROOT / "update_manifest.json"
    existing = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    hashes = {name: sha256(ROOT / name) for name in RELEASE_FILES}
    hash_order = [name for name in existing.get("sha256", {}) if name in hashes]
    hash_order.extend(name for name in RELEASE_FILES if name not in hash_order)
    manifest = {
        "version": version,
        "notes": existing.get("notes") or f"Prompt Atelier V{version}；发布清单由本地工具自动生成，包含文件完整性校验。",
        "files": RELEASE_FILES,
        "sha256": {name: hashes[name] for name in hash_order},
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"已生成更新清单：V{version}")
    print(f"已校验并记录 {len(RELEASE_FILES)} 个发布文件的 SHA-256。")

if __name__ == "__main__":
    main()
