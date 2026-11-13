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


def _is_junction(path: Path) -> bool:
    checker = getattr(path, "is_junction", None)
    try:
        return bool(checker()) if callable(checker) else False
    except OSError:
        return True


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
                previous: dict[str, dict] | None = None,
                force_hash: bool = False) -> list[dict]:
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
            relative = candidate.relative_to(root)
            if any(part.startswith(".") for part in relative.parts):
                continue
            if candidate.suffix.lower() not in allowed:
                continue
            # Never traverse a symlink or Windows junction hidden in the selected tree.
            parent = root
            linked_parent = False
            for part in relative.parts[:-1]:
                parent = parent / part
                if parent.is_symlink() or _is_junction(parent):
                    linked_parent = True
                    break
            if linked_parent or candidate.is_symlink() or _is_junction(candidate):
                continue
            if not candidate.is_file():
                continue
            canonical_path = candidate.resolve(strict=True)
            try:
                canonical_path.relative_to(root)
            except ValueError:
                # A junction/reparse point resolved outside the selected source folder.
                continue
            stat = canonical_path.stat()
            if stat.st_size <= 0:
                continue
            canonical = str(canonical_path)
            old = (previous or {}).get(canonical)
            unchanged = bool(not force_hash and old and old.get("size") == stat.st_size
                             and old.get("modified_ns") == stat.st_mtime_ns
                             and old.get("sha256"))
            found.append({
                "path": canonical,
                "relative_path": relative.as_posix(),
                "name": candidate.name,
                "extension": candidate.suffix.lower(),
                "media_type": classify(candidate),
                "size": stat.st_size,
                "modified_ns": stat.st_mtime_ns,
                "sha256": old["sha256"] if unchanged else sha256_file(canonical_path),
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
