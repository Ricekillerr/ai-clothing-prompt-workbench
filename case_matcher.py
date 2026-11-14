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


_MATCH_WEIGHTS = {"category": 0.25, "material": 0.20, "color": 0.15, "details": 0.20, "context": 0.20}
_MATCH_TERMS = {
 "category": {
  "上装","上衣","下装","鞋履","鞋子","配饰","整套搭配","马甲","背心","夹克","羽绒服","外套","衬衫","卫衣","毛衣",
  "裤子","长裤","短裤","运动短裤","牛仔裤","运动裤","直筒裤","阔腿裤","微喇裤","裙子","半身裙","连衣裙","连体裤",
  "短款","长款","oversized","relaxed","slim","wide-leg","flare",
  "运动鞋","皮鞋","靴子","短靴","长靴","凉鞋","高跟鞋",
  "背包","双肩包","手提包","围巾","帽子","手套","腰带","戒指","眼镜","手表",
  "gilet","vest","jacket","coat","shirt","pants","shoes","sneakers","boots","sandals","bag","backpack","scarf","hat","gloves","belt","ring","glasses","watch"
 },
 "material": {"面料","材质","羊毛","羊绒","腈纶","聚酯","涤纶","棉","皮革","真皮","灯芯绒","尼龙","锦纶","羽绒","针织","牛仔","丝绸","真丝","亚麻","哑光","磨砂","光泽","反光","柔软","挺括","corduroy","leather","wool","cotton","nylon","polyester","matte"},
 "color": {"黑色","白色","灰色","深灰","浅灰","棕色","咖啡色","蓝色","藏青","绿色","红色","米色","米白","卡其","black","white","grey","gray","brown","blue","navy","green","red","beige"},
 "details": {"立领","高领","圆领","直筒","阔腿","微喇","拉链","双拉头","单拉头","双针","双针走线","口袋","袖口","下摆","绗缝","拼接","皮革边","无袖","微喇","宽松","修身","走线","抽绳","帽子","连帽","领口","纽扣","zipper","collar","pocket","hood","sleeveless","hem","stitch"},
 "context": {"换装","人物锁定","动作锁定","镜头锁定","近景","中景","远景","街道","湖边","车内","室内","户外","runninghub","wan","seedance","h3","minimax","视频","图片","参考图","场景"}
}
def _match_text(value):
    if value is None: return ""
    if isinstance(value,(list,tuple,set)): return " ".join(_match_text(x) for x in value)
    if isinstance(value,dict): return " ".join(_match_text(x) for x in value.values())
    return str(value)
def _match_tokens(value):
    text=_match_text(value).casefold()
    aliases = {"锦纶": "尼龙", "涤纶": "聚酯", "真丝": "丝绸", "米白": "米色", "藏青": "蓝色"}
    for alias, canonical in aliases.items():
        text = text.replace(alias, canonical)
    tokens=set(re.findall(r"[a-z0-9][a-z0-9._-]*",text))
    for run in re.findall(r"[\u4e00-\u9fff]+",text):
        if len(run)<=4: tokens.add(run)
        for size in (2,3,4): tokens.update(run[i:i+size] for i in range(max(0,len(run)-size+1)))
    return {x for x in tokens if len(x)>1}
def _query_attribute_groups(query):
    lowered=query.casefold()
    groups={}
    matched_terms=set()
    for dim,terms in _MATCH_TERMS.items():
        found=set()
        for term in terms:
            # English aliases require whole-word matches; Chinese aliases remain phrase matches.
            if _contains_alias(lowered, term):
                found.update(_match_tokens(term))
                matched_terms.add(term)
        groups[dim]=found
    # Remove recognized phrases before extracting generic Chinese n-grams.
    # Otherwise cross-boundary fragments such as "色尼" from "黑色尼龙"
    # become artificial missing terms and unfairly lower an otherwise complete match.
    generic_text=lowered
    for term in sorted(matched_terms,key=len,reverse=True):
        if re.fullmatch(r"[a-z]+",term.casefold()):
            generic_text=re.sub(r"(?<![a-z])"+re.escape(term.casefold())+r"(?![a-z])"," ",generic_text)
        else:
            generic_text=generic_text.replace(term.casefold()," ")
    return groups,_match_tokens(generic_text)
def _candidate_attribute_text(case):
    tags=_match_text(case.get("tags")); body=_match_text(case.get("body"))
    def vals(*keys): return _match_text([case.get(k) for k in keys])
    outfit_items = case.get("outfit_items") if isinstance(case.get("outfit_items"), list) else []
    item_text = _match_text(outfit_items)
    item_values = lambda *keys: _match_text([
        item.get(key) for item in outfit_items if isinstance(item, dict) for key in keys
    ])
    return {
        "category": vals("category","subcategory","silhouette","fit","name")+" "+item_values("category","subcategory","name","silhouette","fit")+" "+tags,
        # A structured outfit item may have only its display name populated.
        # Its name/subcategory are still item-scoped evidence for color/material
        # and details; otherwise a valid single-item query is unfairly penalized.
        "material": vals("material","fabric","texture","material_details")+" "+item_values("material","name","subcategory")+" "+tags,
        "color": vals("color","color_details")+" "+item_values("color","name","subcategory")+" "+tags,
        "details": vals("details","craftsmanship","features","subcategory","silhouette","fit")+" "+item_values("details","name","subcategory","silhouette","fit")+" "+tags,
        "context": vals("model","scene","constraints","usage","notes")+" "+tags,
        "all": _match_text(case)+" "+body+" "+item_text+" "+tags,
    }
def score_case_attributes(case,query):
    query=str(query or "").strip()
    if not query: return {"score":0.0,"reasons":["请输入款号或服装描述"]}
    sku=_match_text(case.get("sku") or case.get("style_number") or case.get("product_code")).strip().casefold()
    if sku and re.search(r"(?<![a-z0-9])"+re.escape(sku)+r"(?![a-z0-9])",query.casefold()):
        return {"score":1.0,"reasons":["款号精确匹配"]}
    groups,generic=_query_attribute_groups(query); candidate=_candidate_attribute_text(case)
    active=[(name,tokens) for name,tokens in groups.items() if tokens]
    if not active:
        qt=_match_tokens(query); ct=_match_tokens(candidate["all"])
        return {"score":round(len(qt&ct)/max(1,len(qt)),4),"reasons":["描述关键词覆盖率"]}
    weighted=total=0.0; reasons=[]
    labels={"category":"品类/版型匹配","material":"面料/材质匹配","color":"颜色匹配","details":"结构/细节匹配","context":"生成场景/约束匹配"}
    for name,tokens in active:
        ct=_match_tokens(candidate[name]); coverage=len(tokens&ct)/max(1,len(tokens))
        weighted+=coverage*_MATCH_WEIGHTS[name]; total+=_MATCH_WEIGHTS[name]
        if coverage>=0.5: reasons.append(labels[name])
    if generic:
        ct=_match_tokens(candidate["all"]); coverage=len(generic&ct)/max(1,len(generic))
        weighted+=coverage*0.10; total+=0.10
        if coverage>=0.5: reasons.append("其他描述关键词匹配")
    return {"score":round(weighted/max(total,1e-9),4),"reasons":reasons or ["匹配属性较少，建议人工核对"]}
_EXCLUSIVE_COLORS = {
    "black": ("黑色", "black"),
    "white": ("白色", "white"),
    "red": ("红色", "red"),
    "blue": ("蓝色", "藏青", "blue", "navy"),
    "green": ("绿色", "green"),
    "brown": ("棕色", "咖啡色", "brown"),
    "grey": ("灰色", "grey", "gray"),
    "beige": ("米色", "米白", "beige"),
}

_EXCLUSIVE_MATERIALS = {
    "nylon": ("尼龙", "锦纶", "nylon"),
    "polyester": ("聚酯", "涤纶", "polyester"),
    "acrylic": ("腈纶", "acrylic"),
    "leather": ("皮革", "皮质", "真皮", "leather"),
    "corduroy": ("灯芯绒", "corduroy"),
    "wool": ("羊毛", "wool"),
    "cashmere": ("羊绒", "cashmere"),
    "cotton": ("棉", "棉质", "cotton"),
    "denim": ("牛仔", "denim"),
    "silk": ("真丝", "丝绸", "silk"),
    "linen": ("亚麻", "linen"),
}
_EXCLUSIVE_CATEGORIES = {
    "upper": ("上装", "上衣", "上身", "马甲", "背心", "夹克", "羽绒服", "外套",
              "衬衫", "卫衣", "T恤", "针织衫", "毛衣", "大衣", "西装",
              "gilet", "vest", "jacket", "coat", "overcoat", "shirt",
              "hoodie", "sweatshirt", "cardigan", "puffer jacket"),
    "lower": ("下装", "裤子", "长裤", "短裤", "直筒裤", "阔腿裤", "微喇裤",
              "牛仔裤", "运动裤", "裙子", "半身裙", "pants", "trousers",
              "jeans", "shorts", "leggings", "skirt"),
    "footwear": ("鞋履", "鞋子", "运动鞋", "皮鞋", "靴子", "凉鞋",
                 "sneakers", "shoes", "boots", "sandals"),
    "accessory": ("配饰", "腰带", "帽子", "围巾", "包", "戒指", "眼镜", "手表",
                  "belt", "hat", "scarf", "bag", "ring", "glasses", "watch"),
    "onepiece": ("连体装", "连体裤", "连衣裙", "dress", "jumpsuit", "romper"),
    "full_outfit": ("整套搭配", "整套穿搭", "全套穿搭", "整套服装", "full outfit"),
}
_MIXTURE_MARKERS = ("混纺", "混合", "拼接", "复合", "blend", "mixed", "mix")



def _contains_alias(text, alias):
    """Match English aliases as words; Chinese aliases as explicit phrases."""
    text = str(text or "").casefold()
    alias = alias.casefold()
    if re.fullmatch(r"[a-z]+", alias):
        return re.search(r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])", text) is not None
    return alias in text


def _color_values(text):
    return {
        key for key, aliases in _EXCLUSIVE_COLORS.items()
        if any(_contains_alias(text, alias) for alias in aliases)
    }


def _exclusive_values(text, vocabulary):
    return {
        key for key, aliases in vocabulary.items()
        if any(_contains_alias(text, alias) for alias in aliases)
    }


def find_case_conflicts(case, query):
    """Flag unambiguous category, color and material contradictions.

    Category evidence comes from structured case fields and outfit-item breakdowns.
    A full-outfit case remains eligible for an individual garment query because it
    may contain that garment even when its breakdown is incomplete. Multi-color and
    mixed-material records are intentionally left for human review. Prompt body and
    tags are excluded from hard conflict checks to avoid scene/background false positives.
    """
    conflicts = []
    requested_categories = _exclusive_values(query, _EXCLUSIVE_CATEGORIES)
    outfit_items = case.get("outfit_items") if isinstance(case.get("outfit_items"), list) else []
    stored_category_text = _match_text([
        case.get("category"), case.get("subcategory"), case.get("name"),
        [
            {key: item.get(key) for key in ("category", "subcategory", "name")}
            for item in outfit_items if isinstance(item, dict)
        ],
    ])
    stored_categories = _exclusive_values(stored_category_text, _EXCLUSIVE_CATEGORIES)
    # Do not guess the contents of an unstructured "full outfit" record.
    if requested_categories and stored_categories and "full_outfit" not in stored_categories:
        missing_categories = requested_categories - stored_categories
        if missing_categories:
            conflicts.append({
                "attribute": "category",
                "requested": sorted(requested_categories),
                "case_value": sorted(stored_categories),
                "reason": "查询要求与案例结构化品类冲突",
            })

    requested_colors = _color_values(query)
    stored_colors = _color_values(_match_text([
        case.get("color"), case.get("color_details"),
        [item.get("color") for item in outfit_items if isinstance(item, dict)],
    ]))
    if len(requested_colors) == 1 and len(stored_colors) == 1:
        wanted, actual = next(iter(requested_colors)), next(iter(stored_colors))
        if wanted != actual:
            conflicts.append({
                "attribute": "color", "requested": wanted, "case_value": actual,
                "reason": "查询要求与案例结构化颜色字段冲突",
            })

    query_text = str(query or "").casefold()
    stored_material_text = _match_text([
        case.get("material"), case.get("fabric"), case.get("texture"),
        case.get("material_details"),
        [item.get("material") for item in outfit_items if isinstance(item, dict)],
    ]).casefold()
    requested_materials = _exclusive_values(query_text, _EXCLUSIVE_MATERIALS)
    stored_materials = _exclusive_values(stored_material_text, _EXCLUSIVE_MATERIALS)
    query_is_mixed = any(marker in query_text for marker in _MIXTURE_MARKERS)
    stored_is_mixed = any(marker in stored_material_text for marker in _MIXTURE_MARKERS)
    if (len(requested_materials) == 1 and len(stored_materials) == 1
            and not query_is_mixed and not stored_is_mixed):
        wanted, actual = next(iter(requested_materials)), next(iter(stored_materials))
        if wanted != actual:
            conflicts.append({
                "attribute": "material", "requested": wanted, "case_value": actual,
                "reason": "查询要求与案例结构化面料字段冲突",
            })
    return conflicts


def explain_case_match(case, query):
    """Return transparent per-attribute coverage; never label ranking as confidence."""
    query = str(query or "").strip()
    if not query:
        return []
    groups, generic = _query_attribute_groups(query)
    candidate = _candidate_attribute_text(case)
    labels = {
        "category": "品类/版型",
        "material": "面料/材质",
        "color": "颜色",
        "details": "结构/细节",
        "context": "模型/场景/约束",
    }
    details = []
    for name, terms in groups.items():
        if not terms:
            continue
        available = _match_tokens(candidate[name])
        matched = sorted(terms & available)
        missing = sorted(terms - available)
        details.append({
            "attribute": name,
            "label": labels[name],
            "query_terms": sorted(terms),
            "matched_terms": matched,
            "missing_terms": missing,
            "coverage": round(len(matched) / max(1, len(terms)), 4),
        })
    if generic:
        available = _match_tokens(candidate["all"])
        matched = sorted(generic & available)
        missing = sorted(generic - available)
        details.append({
            "attribute": "other",
            "label": "其他描述",
            "query_terms": sorted(generic),
            "matched_terms": matched,
            "missing_terms": missing,
            "coverage": round(len(matched) / max(1, len(generic)), 4),
        })
    return details


def _outfit_item_candidate(case, item):
    """Build isolated garment evidence so attributes from sibling items cannot mix."""
    return {
        "name": item.get("name") or item.get("subcategory") or item.get("category") or "",
        "category": item.get("category"),
        "subcategory": item.get("subcategory"),
        "silhouette": item.get("silhouette"),
        "fit": item.get("fit"),
        "material": item.get("material") or item.get("name") or item.get("subcategory"),
        "fabric": item.get("fabric"),
        "texture": item.get("texture"),
        "material_details": item.get("material_details"),
        "color": item.get("color") or item.get("name") or item.get("subcategory"),
        "color_details": item.get("color_details"),
        "details": item.get("details") or item.get("name") or item.get("subcategory"),
        "craftsmanship": item.get("craftsmanship"),
        "features": item.get("features"),
        "tags": item.get("tags") if isinstance(item.get("tags"), (list, tuple, set)) else [],
        "model": case.get("model"),
        "scene": case.get("scene"),
        "constraints": case.get("constraints"),
        "usage": case.get("usage"),
        "notes": case.get("notes"),
        "sku": case.get("sku") or case.get("style_number") or case.get("product_code"),
    }


def _case_match_candidates(case, query):
    """Use one garment at a time for multi-item outfits, except whole-outfit/SKU searches."""
    items = case.get("outfit_items") if isinstance(case.get("outfit_items"), list) else []
    sku = _match_text(case.get("sku") or case.get("style_number") or case.get("product_code")).strip().casefold()
    exact_sku = bool(sku and re.search(r"(?<![a-z0-9])" + re.escape(sku) + r"(?![a-z0-9])", str(query or "").casefold()))
    requested_categories = _exclusive_values(query, _EXCLUSIVE_CATEGORIES)
    if len(items) > 1 and "full_outfit" not in requested_categories and not exact_sku:
        candidates = [
            (_outfit_item_candidate(case, item), item)
            for item in items if isinstance(item, dict)
        ]
        if candidates:
            return candidates
    return [(case, None)]


def rank_case_matches(cases,query,threshold=0.80,limit=100,include_unverified=False,include_conflicts=False):
    try: threshold=min(1.0,max(0.0,float(threshold)))
    except (TypeError,ValueError): threshold=0.80
    results=[]
    for case in cases:
        if not isinstance(case,dict): continue
        if not include_unverified and case.get("status")!="已验证成功": continue
        evaluated=[]
        for candidate,item in _case_match_candidates(case, query):
            conflicts=find_case_conflicts(candidate,query)
            scored=score_case_attributes(candidate,query)
            evaluated.append((candidate,item,conflicts,scored))
        if not evaluated:
            continue
        if include_conflicts:
            chosen=max(evaluated,key=lambda entry:entry[3]["score"])
        else:
            compatible=[entry for entry in evaluated if not entry[2]]
            if not compatible:
                continue
            chosen=max(compatible,key=lambda entry:entry[3]["score"])
        candidate,item,conflicts,scored=chosen
        if scored["score"]>=threshold:
            item_label=None
            if isinstance(item,dict):
                item_label=str(item.get("name") or item.get("subcategory") or item.get("category") or "").strip() or None
            results.append({
                "case":case,
                "score":scored["score"],
                "match_percent":round(scored["score"]*100),
                "reasons":scored["reasons"],
                "attribute_details":explain_case_match(candidate,query),
                "conflicts":conflicts,
                "matched_item":item_label,
            })
    results.sort(key=lambda x:(-x["score"],str(x["case"].get("name",""))))
    return results[:max(0,int(limit))]
