# -*- coding: utf-8 -*-
"""Explainable attribute-based retrieval for verified clothing prompt cases.

This is a deterministic local baseline, not an embedding model. Scores are
query-attribute coverage estimates and are shown with matching reasons.
"""
from __future__ import annotations

import re
from typing import Any

WEIGHTS = {
    "category": 0.25,
    "material": 0.20,
    "color": 0.15,
    "details": 0.20,
    "context": 0.20,
}

CATEGORY_WORDS = {
    "上装", "下装", "鞋", "鞋履", "配饰", "马甲", "背心", "夹克", "羽绒服",
    "外套", "衬衫", "卫衣", "裤子", "长裤", "短裤", "运动鞋", "整套搭配",
    "gilet", "vest", "jacket", "coat", "shirt", "pants", "trousers", "shoes",
}
MATERIAL_WORDS = {
    "面料", "材质", "羊毛", "羊绒", "腈纶", "聚酯", "棉", "皮革", "真皮",
    "灯芯绒", "尼龙", "羽绒", "针织", "牛仔", "丝绸", "哑光", "磨砂",
    "光泽", "反光", "柔软", "挺括", "高密度", "corduroy", "leather",
    "wool", "cotton", "nylon", "polyester", "matte",
}
COLOR_WORDS = {
    "黑色", "白色", "灰色", "深灰", "浅灰", "棕色", "咖啡色", "蓝色",
    "藏青", "绿色", "红色", "米色", "卡其", "black", "white", "grey",
    "gray", "brown", "blue", "navy", "green", "red", "beige",
}
DETAIL_WORDS = {
    "立领", "高领", "圆领", "拉链", "双拉头", "单拉头", "口袋", "袖口",
    "下摆", "绗缝", "绗线", "拼接", "皮革边", "无袖", "微喇", "宽松",
    "修身", "走线", "抽绳", "帽子", "连帽", "领口", "纽扣", "拉链敞开",
    "zipper", "collar", "pocket", "hood", "sleeveless", "hem", "stitch",
}
CONTEXT_WORDS = {
    "换装", "人物锁定", "动作锁定", "镜头锁定", "近景", "中景", "远景",
    "街道", "湖边", "车内", "室内", "户外", "wan", "runninghub",
    "minimax", "h3", "视频", "图片", "参考图", "场景",
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return " ".join(_text(x) for x in value)
    if isinstance(value, dict):
        return " ".join(_text(x) for x in value.values())
    return str(value)


def _tokens(value: Any) -> set[str]:
    text = _text(value).casefold()
    english = re.findall(r"[a-z0-9][a-z0-9._-]*", text)
    chinese_runs = re.findall(r"[\u4e00-\u9fff]+", text)
    tokens = set(english)
    for run in chinese_runs:
        if len(run) <= 4:
            tokens.add(run)
        for size in (2, 3, 4):
            tokens.update(run[i:i + size] for i in range(max(0, len(run) - size + 1)))
    return {x for x in tokens if len(x) > 1}


def _contains_any(text: str, words: set[str]) -> set[str]:
    lowered = text.casefold()
    found: set[str] = set()
    for word in words:
        if word.casefold() in lowered:
            found.update(_tokens(word))
    return found


def _case_values(case: dict, keys: tuple[str, ...]) -> str:
    return " ".join(_text(case.get(key)) for key in keys)


def _query_groups(query: str) -> tuple[dict[str, set[str]], set[str]]:
    groups = {
        "category": _contains_any(query, CATEGORY_WORDS),
        "material": _contains_any(query, MATERIAL_WORDS),
        "color": _contains_any(query, COLOR_WORDS),
        "details": _contains_any(query, DETAIL_WORDS),
        "context": _contains_any(query, CONTEXT_WORDS),
    }
    classified = set().union(*groups.values())
    generic = _tokens(query) - classified
    return groups, generic


def _coverage(query_tokens: set[str], candidate_text: str) -> float:
    if not query_tokens:
        return 0.0
    candidate_tokens = _tokens(candidate_text)
    if not candidate_tokens:
        return 0.0
    return len(query_tokens & candidate_tokens) / len(query_tokens)


def _candidate_groups(case: dict) -> dict[str, str]:
    tags = _text(case.get("tags"))
    body = _text(case.get("body"))
    return {
        "category": _case_values(case, ("category", "subcategory", "silhouette", "fit", "name")) + " " + tags,
        "material": _case_values(case, ("material", "fabric", "texture", "material_details")) + " " + tags,
        "color": _case_values(case, ("color", "color_details")) + " " + tags,
        "details": _case_values(case, ("details", "craftsmanship", "features", "subcategory")) + " " + tags,
        "context": _case_values(case, ("model", "scene", "constraints", "usage", "notes")) + " " + tags,
        "all": _case_values(case, ("name", "category", "subcategory", "material", "color", "model",
                                   "silhouette", "fit", "details", "craftsmanship", "features",
                                   "scene", "constraints", "body", "notes", "sku", "style_number")) + " " + tags + " " + body,
    }


def score_case(case: dict, query: str) -> dict:
    """Return an explainable 0..1 score for one case."""
    q = str(query or "").strip()
    if not q:
        return {"score": 0.0, "reasons": ["请输入款号或服装描述"]}
    sku = _text(case.get("sku") or case.get("style_number") or case.get("product_code")).strip().casefold()
    if sku and re.search(r"(?<![a-z0-9])" + re.escape(sku) + r"(?![a-z0-9])", q.casefold()):
        return {"score": 1.0, "reasons": ["款号精确匹配"]}

    groups, generic = _query_groups(q)
    candidate = _candidate_groups(case)
    active = [(name, tokens) for name, tokens in groups.items() if tokens]
    if not active:
        score = _coverage(_tokens(q), candidate["all"])
        reasons = ["描述关键词覆盖率"]
        return {"score": round(score, 4), "reasons": reasons}

    weighted = 0.0
    weight_total = 0.0
    reasons: list[str] = []
    for name, tokens in active:
        value = _coverage(tokens, candidate[name])
        weighted += value * WEIGHTS[name]
        weight_total += WEIGHTS[name]
        if value >= 0.5:
            reasons.append({
                "category": "品类/版型匹配",
                "material": "面料/材质匹配",
                "color": "颜色匹配",
                "details": "结构/细节匹配",
                "context": "生成场景/约束匹配",
            }[name])
    if generic:
        value = _coverage(generic, candidate["all"])
        weighted += value * 0.10
        weight_total += 0.10
        if value >= 0.5:
            reasons.append("其他描述关键词匹配")
    score = weighted / weight_total if weight_total else 0.0
    return {"score": round(score, 4), "reasons": reasons or ["匹配属性较少，建议人工核对"]}


def rank_case_matches(cases: list[dict], query: str, threshold: float = 0.8,
                      limit: int = 100, include_unverified: bool = False) -> list[dict]:
    """Rank verified cases by default; only return scores at/above threshold."""
    try:
        threshold = min(1.0, max(0.0, float(threshold)))
    except (TypeError, ValueError):
        threshold = 0.8
    results = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        if not include_unverified and case.get("status") != "已验证成功":
            continue
        scored = score_case(case, query)
        if scored["score"] >= threshold:
            results.append({
                "case": case,
                "score": scored["score"],
                "match_percent": round(scored["score"] * 100),
                "reasons": scored["reasons"],
            })
    results.sort(key=lambda x: (-x["score"], str(x["case"].get("name", ""))))
    return results[:max(0, int(limit))]
