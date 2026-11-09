# -*- coding: utf-8 -*-
"""Incremental, read-only local history folder scanner."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Iterable

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".avi"}
TEXT_EXTENSIONS = {".txt", ".md", ".json"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | TEXT_EXTENSIONS
CHUNK_SIZE = 1024 * 1024


def classify(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in TEXT_EXTENSIONS:
        return "text"
    return "unsupported"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan_folder(folder: Path, recursive: bool = True,
                extensions: Iterable[str] | None = None,
                previous: dict[str, dict] | None = None) -> list[dict]:
    """Return metadata for supported files; never move, rename, or modify sources."""
    root = Path(folder).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"扫描路径不是文件夹：{root}")
    allowed = {x.lower() if x.startswith(".") else "." + x.lower()
               for x in (extensions or SUPPORTED_EXTENSIONS)}
    iterator = root.rglob("*") if recursive else root.iterdir()
    found = []
    for candidate in iterator:
        try:
            if candidate.is_symlink() or not candidate.is_file():
                continue
            if any(part.startswith(".") for part in candidate.relative_to(root).parts):
                continue
            if candidate.suffix.lower() not in allowed:
                continue
            stat = candidate.stat()
            if stat.st_size <= 0:
                continue
            canonical = str(candidate.resolve())
            old = (previous or {}).get(canonical)
            unchanged = bool(old and old.get("size") == stat.st_size
                             and old.get("modified_ns") == stat.st_mtime_ns
                             and old.get("sha256"))
            found.append({
                "path": canonical,
                "relative_path": candidate.relative_to(root).as_posix(),
                "name": candidate.name,
                "extension": candidate.suffix.lower(),
                "media_type": classify(candidate),
                "size": stat.st_size,
                "modified_ns": stat.st_mtime_ns,
                "sha256": old["sha256"] if unchanged else sha256_file(candidate),
            })
        except (OSError, ValueError):
            # Files changing during a scan are retried on the next scan.
            continue
    found.sort(key=lambda item: (item["relative_path"].casefold(), item["sha256"]))
    return found


def find_new_or_changed(previous: dict[str, dict], current: list[dict]) -> list[dict]:
    """Compare scan snapshots by canonical path, size, mtime and content hash."""
    changes = []
    for item in current:
        old = previous.get(item["path"])
        if old is None or any(old.get(key) != item.get(key)
                              for key in ("size", "modified_ns", "sha256")):
            changes.append(item)
    return changes
