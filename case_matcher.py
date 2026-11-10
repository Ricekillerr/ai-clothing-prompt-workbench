# -*- coding: utf-8 -*-
"""Conservative local matching suggestions for imported history assets.

This module never auto-links assets. It ranks likely image/video/prompt groups
for the AI analysis and human-review stages.
"""
from __future__ import annotations

import re
from pathlib import PurePath
from difflib import SequenceMatcher


def _tokens(value: str) -> set[str]:
    value = value.casefold()
    value = re.sub(r"\.(png|jpe?g|webp|gif|bmp|mp4|mov|m4v|webm|avi|txt|md|json)$", "", value)
    return {x for x in re.split(r"[^a-z0-9\u4e00-\u9fff]+", value) if len(x) > 1}


def _stem(path: str) -> str:
    return PurePath(path.replace("\\", "/")).stem.casefold()


def _directory(path: str) -> str:
    return str(PurePath(path.replace("\\", "/")).parent).casefold()


def similarity(a: str, b: str) -> float:
    sa, sb = _stem(a), _stem(b)
    if not sa or not sb:
        return 0.0
    seq = SequenceMatcher(None, sa, sb).ratio()
    ta, tb = _tokens(sa), _tokens(sb)
    overlap = len(ta & tb) / max(1, len(ta | tb))
    return max(seq, overlap)


def rank_matches(items: list[dict], limit: int = 100) -> list[dict]:
    """Rank prompt-to-media pairs; scores are suggestions, not decisions."""
    prompts = [x for x in items if x.get("media_type") == "text"]
    media = [x for x in items if x.get("media_type") in {"image", "video"}]
    candidates = []
    for prompt in prompts:
        for asset in media:
            name_score = similarity(prompt.get("name", ""), asset.get("name", ""))
            same_dir = _directory(prompt.get("path", "")) == _directory(asset.get("path", ""))
            # Same folder is useful context but cannot create a match alone.
            score = min(1.0, name_score * 0.82 + (0.18 if same_dir else 0.0))
            if name_score >= 0.34:
                candidates.append({
                    "prompt_path": prompt.get("path"),
                    "asset_path": asset.get("path"),
                    "asset_type": asset.get("media_type"),
                    "score": round(score, 4),
                    "reason": "文件名相似" + ("；位于同一文件夹" if same_dir else ""),
                    "status": "suggested",
                })
    candidates.sort(key=lambda x: (-x["score"], str(x["prompt_path"]), str(x["asset_path"])))
    return candidates[:max(0, limit)]
