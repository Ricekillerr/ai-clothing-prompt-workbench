# -*- coding: utf-8 -*-
"""Optional OpenAI-compatible analysis adapter for local history ingestion.

API credentials are supplied per request and are never written to disk.
"""
from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen


MAX_TEXT_BYTES = 1_000_000
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _safe_endpoint(endpoint: str) -> str:
    endpoint = str(endpoint or "").strip().rstrip("/")
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValueError("API 地址必须是有效的 HTTP(S) 地址")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("非本机 API 必须使用 HTTPS，避免 API Key 在网络中明文传输")
    if parsed.username or parsed.password:
        raise ValueError("API 地址中不能嵌入账号或密码")
    if not endpoint.endswith("/chat/completions"):
        endpoint += "/chat/completions"
    return endpoint


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    if len(raw) > MAX_TEXT_BYTES:
        raise ValueError("提示词文件超过 1MB，暂不发送分析")
    return raw.decode("utf-8-sig", errors="replace")


def normalize_asset_roles(raw_roles, image_files: list[Path]) -> list[dict]:
    """Return one bounded, ordered role record per image; missing AI labels stay unknown."""
    roles_by_index = {}
    if isinstance(raw_roles, list):
        for role in raw_roles:
            if not isinstance(role, dict):
                continue
            try:
                index = int(role.get("asset_index", 0))
            except (TypeError, ValueError):
                continue
            if 1 <= index <= len(image_files) and index not in roles_by_index:
                roles_by_index[index] = role
    allowed_roles = {"overall", "front", "back", "detail", "fabric", "label", "scene", "other", "unknown"}
    normalized = []
    for index, image_file in enumerate(image_files, start=1):
        role = roles_by_index.get(index, {})
        value = str(role.get("role") or "unknown").strip().lower()
        if value not in allowed_roles:
            value = "unknown"
        raw_priority = str(role.get("priority") or "").strip().lower()
        default_priority = (
            "primary" if value in {"overall", "front", "back"} else
            "secondary" if value in {"detail", "fabric"} else "supporting"
        )
        priority = raw_priority if raw_priority in {"primary", "secondary", "supporting"} else default_priority
        raw_governs = role.get("governs") if isinstance(role.get("governs"), list) else []
        normalized.append({
            "asset_index": index,
            "asset_path": str(image_file),
            "role": value,
            "role_label": str(role.get("role_label") or ("待人工确认" if value == "unknown" else value))[:40],
            "priority": priority,
            "governs": [str(item)[:40] for item in raw_governs[:6] if str(item).strip()],
            "observation": str(role.get("observation") or "未提供可靠的单图观察")[:500],
        })
    return normalized


def normalize_outfit_items(raw_items, raw_relations=None) -> tuple[list[dict], list[dict]]:
    """Normalize AI garment breakdown into bounded, explicit item records."""
    allowed_categories = {"上装", "下装", "鞋履", "配饰", "连体装", "其他"}
    items = []
    if isinstance(raw_items, list):
        for raw in raw_items[:48]:
            if len(items) >= 12:
                break
            if not isinstance(raw, dict):
                continue
            category = str(raw.get("category") or "其他").strip()[:20]
            if category not in allowed_categories:
                category = "其他"
            def clean(value, limit=120):
                if isinstance(value, (list, tuple)):
                    value = "、".join(str(x) for x in value[:20])
                return str(value or "").strip()[:limit]
            details = raw.get("details") if isinstance(raw.get("details"), list) else []
            items.append({
                "item_index": len(items) + 1,
                "category": category,
                "subcategory": clean(raw.get("subcategory"), 60),
                "name": clean(raw.get("name"), 120),
                "color": clean(raw.get("color"), 60),
                "material": clean(raw.get("material"), 120),
                "silhouette": clean(raw.get("silhouette"), 80),
                "fit": clean(raw.get("fit"), 60),
                "details": [clean(value, 100) for value in details[:20] if clean(value, 100)],
                "evidence": clean(raw.get("evidence"), 300),
                "confidence": max(0.0, min(1.0, float(raw.get("confidence", 0) or 0)))
                    if str(raw.get("confidence", 0) or 0).replace(".", "", 1).isdigit() else 0.0,
            })
    relations = []
    if isinstance(raw_relations, list):
        allowed_relations = {"over", "under", "paired_with", "worn_with", "attached_to", "unknown"}
        for raw in raw_relations[:20]:
            if not isinstance(raw, dict):
                continue
            relation = str(raw.get("relation") or "unknown").strip().lower()
            if relation not in allowed_relations:
                relation = "unknown"
            try:
                source = int(raw.get("source_item", 0))
                target = int(raw.get("target_item", 0))
            except (TypeError, ValueError):
                continue
            if not (1 <= source <= len(items) and 1 <= target <= len(items)) or source == target:
                continue
            relations.append({
                "source_item": source,
                "target_item": target,
                "relation": relation,
                "evidence": str(raw.get("evidence") or "").strip()[:240],
            })
    return items, relations


def analyze_assets(prompt_path: str, asset_paths: list[str], api_key: str,
                  endpoint: str = "https://api.openai.com/v1",
                  model: str = "gpt-4o-mini", timeout: int = 120) -> dict:
    prompt_file = Path(prompt_path).resolve()
    assets = [Path(value).resolve() for value in asset_paths]
    if not prompt_file.is_file():
        raise ValueError("提示词文件不存在")
    if not 1 <= len(assets) <= 8:
        raise ValueError("每次必须选择 1–8 张关联素材")
    if len({str(path) for path in assets}) != len(assets):
        raise ValueError("关联素材不能重复")
    if any(not path.is_file() for path in assets):
        raise ValueError("至少一个关联素材文件不存在")
    if not api_key.strip():
        raise ValueError("请填写 API Key；密钥仅在本次请求中使用，不会保存")
    if prompt_file.suffix.lower() not in {".txt", ".md", ".json"}:
        raise ValueError("提示词文件仅支持 TXT、MD、JSON")
    text = _read_text(prompt_file)
    if not text.strip():
        raise ValueError("提示词文件内容为空")

    instructions = (
        "你是 AI 视频生成案例整理助手。根据一份原始提示词和同一套服装/场景的多张关联素材，"
        "综合识别信息；不同角度互相补充，不要把同一件衣服误拆成多个案例。"
        "输出严格 JSON，不要 Markdown。字段：title（简短标题）、sku（提示词明确出现的服装款号；没有则空字符串）、"
        "category（服装/人物/场景/其他）、subcategory（具体服装品类）、material（面料材质）、color（颜色）、"
        "silhouette（版型轮廓）、fit（宽松/修身等版型）、details（结构与工艺细节字符串数组）、"
        "scene（生成场景）、constraints（生成约束字符串数组）、tags（字符串数组）、"
        "prompt_summary（中文摘要）、prompt_text（原提示词，尽量逐字保留）、"
        "asset_observations（综合素材可见特征；无法读取时明确写无法判断）、"
        "asset_roles（数组，必须与输入图片顺序和数量完全一致；每项包含 asset_index 从1开始、"
        "role 只能为 overall/front/back/detail/fabric/label/scene/other/unknown、"
        "role_label 中文用途标签、priority 只能为 primary/secondary/supporting、governs 受该图约束的属性数组、"
        "observation 该图可见内容；不能确定时 role=unknown）、"
        "outfit_items（数组，每件可见服装/鞋/配饰一项；字段 category 仅可为上装/下装/鞋履/配饰/连体装/其他，"
        "subcategory、name、color、material、silhouette、fit、details 字符串数组、evidence 可见证据、confidence 0到1）、"
        "outfit_relations（数组，字段 source_item 和 target_item 为 outfit_items 从1开始的序号，"
        "relation 仅可为 over/under/paired_with/worn_with/attached_to/unknown，evidence 为依据；"
        "只有图片/提示词明确支持时才记录层叠关系，不能推测被遮挡的衣物）、"
        "risk_notes（不确定性或潜在误判，字符串数组）、confidence（0到1）。"
        "不要把视频文件计入图片角色数组。属性只记录提示词或图片明确支持的信息；图片之间存在冲突时写入 risk_notes，不要自行猜测。"
    )
    user_content = [{
        "type": "text",
        "text": "待整理提示词：\\n" + text[:50000]
               + f"\\n\\n本次关联素材共 {len(assets)} 个。请综合所有图片的正反面、细节和整体信息。"
    }]
    total_image_bytes = 0
    for index, asset_file in enumerate(assets, start=1):
        ext = asset_file.suffix.lower()
        if ext in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
            size = asset_file.stat().st_size
            if size > MAX_IMAGE_BYTES:
                raise ValueError(f"第 {index} 张图片超过 8MB，暂不发送分析")
            total_image_bytes += size
            if total_image_bytes > 32 * 1024 * 1024:
                raise ValueError("本次图片总量超过 32MB，请减少图片数量或压缩图片")
            raw = asset_file.read_bytes()
            mime = mimetypes.guess_type(asset_file.name)[0] or "image/png"
            data_url = "data:" + mime + ";base64," + base64.b64encode(raw).decode("ascii")
            user_content.append({"type": "text", "text": f"关联图片 {index}：{asset_file.name}"})
            user_content.append({"type": "image_url", "image_url": {"url": data_url, "detail": "high"}})
        elif ext in {".mp4", ".mov", ".m4v", ".webm", ".avi"}:
            user_content.append({
                "type": "text",
                "text": f"关联文件 {index} 是视频：{asset_file.name}。当前版本不会抽帧，不能判断视频画面内容。"
            })
        else:
            raise ValueError(f"不支持的关联素材类型：{asset_file.suffix}")

    payload = {
        "model": model.strip(),
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": user_content},
        ],
    }
    request = Request(
        _safe_endpoint(endpoint),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + api_key.strip(),
                 "Content-Type": "application/json",
                 "User-Agent": "Prompt-Atelier/0.6"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    content = result["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("AI 返回结果不是有效 JSON 对象")
    defaults = {
        "title": prompt_file.stem, "category": "其他", "sku": "", "subcategory": "",
        "material": "", "color": "", "silhouette": "", "fit": "", "details": [],
        "scene": "", "constraints": [], "tags": [], "prompt_summary": "",
        "prompt_text": text, "asset_observations": "未提供", "risk_notes": [],
        "confidence": 0.0,
    }
    for key, value in defaults.items():
        parsed.setdefault(key, value)
    parsed["model"] = model.strip()
    image_files = [path for path in assets if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}]
    parsed["asset_roles"] = normalize_asset_roles(parsed.get("asset_roles"), image_files)
    parsed["outfit_items"], parsed["outfit_relations"] = normalize_outfit_items(
        parsed.get("outfit_items"), parsed.get("outfit_relations")
    )
    parsed["prompt_path"] = str(prompt_file)
    parsed["asset_paths"] = [str(path) for path in assets]
    parsed["asset_path"] = str(assets[0])
    return parsed


def analyze_pair(prompt_path: str, asset_path: str, api_key: str,
                 endpoint: str = "https://api.openai.com/v1",
                 model: str = "gpt-4o-mini", timeout: int = 90) -> dict:
    """Backward-compatible one-asset wrapper."""
    return analyze_assets(prompt_path, [asset_path], api_key, endpoint, model, timeout)
