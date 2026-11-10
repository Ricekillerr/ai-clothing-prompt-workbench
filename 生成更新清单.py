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
    "使用说明_AI服装提示词资产工作台_V0.4.3.txt",
    "storage.py",
    "collector.py",
    "case_matcher.py",
    "ai_analyzer.py",
]

def sha256(path):
    # Git checkouts may convert LF to CRLF on Windows. GitHub serves release files as LF,
    # so normalize line endings before hashing to keep updater hashes cross-platform stable.
    normalized = path.read_bytes().replace(b"\\r\\n", b"\\n")
    h = hashlib.sha256(normalized)
    return h.hexdigest()

def main():
    source = MAIN.read_text(encoding="utf-8-sig")
    match = re.search(r"""^\s*APP_VERSION\s*=\s*["']([^"']+)["']""", source, re.M)
    if not match:
        raise SystemExit("错误：未能从主程序读取 APP_VERSION。")
    version = match.group(1)
    missing = [name for name in RELEASE_FILES if not (ROOT / name).is_file()]
    if missing:
        raise SystemExit("错误：发布文件缺失：" + "、".join(missing))
    manifest = {
        "version": version,
        "notes": f"Prompt Atelier V{version}；发布清单由本地工具自动生成，包含文件完整性校验。",
        "files": RELEASE_FILES,
        "sha256": {name: sha256(ROOT / name) for name in RELEASE_FILES},
    }
    (ROOT / "update_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"已生成更新清单：V{version}")
    print(f"已校验并记录 {len(RELEASE_FILES)} 个发布文件的 SHA-256。")

if __name__ == "__main__":
    main()
