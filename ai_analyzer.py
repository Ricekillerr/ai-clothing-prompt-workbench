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


def analyze_pair(prompt_path: str, asset_path: str, api_key: str,
                 endpoint: str = "https://api.openai.com/v1",
                 model: str = "gpt-4o-mini", timeout: int = 90) -> dict:
    prompt_file, asset_file = Path(prompt_path).resolve(), Path(asset_path).resolve()
    if not prompt_file.is_file() or not asset_file.is_file():
        raise ValueError("待分析文件不存在")
    if not api_key.strip():
        raise ValueError("请填写 API Key；密钥仅在本次请求中使用，不会保存")
    if prompt_file.suffix.lower() not in {".txt", ".md", ".json"}:
        raise ValueError("提示词文件仅支持 TXT、MD、JSON")
    text = _read_text(prompt_file)
    if not text.strip():
        raise ValueError("提示词文件内容为空")
    instructions = (
        "你是 AI 视频生成案例整理助手。根据提供的提示词和关联素材，"
        "输出严格 JSON，不要 Markdown。字段：title（简短标题）、sku（提示词明确出现的服装款号；没有则空字符串）、"
        "category（服装/人物/场景/其他）、subcategory（具体服装品类）、material（面料材质）、color（颜色）、"
        "silhouette（版型轮廓）、fit（宽松/修身等版型）、details（结构与工艺细节字符串数组）、"
        "scene（生成场景）、constraints（生成约束字符串数组）、tags（字符串数组）、"
        "prompt_summary（中文摘要）、prompt_text（原提示词，尽量逐字保留）、"
        "asset_observations（素材可见特征；无法读取时明确写无法判断）、"
        "risk_notes（不确定性或潜在误判，字符串数组）、confidence（0到1）。"
        "属性只记录提示词或图片明确支持的信息；无法判断的字段留空，不要猜测。"
    )
    user_content = [{"type": "text", "text": "待整理提示词：\n" + text[:50000]}]
    ext = asset_file.suffix.lower()
    if ext in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        raw = asset_file.read_bytes()
        if len(raw) > MAX_IMAGE_BYTES:
            raise ValueError("图片超过 8MB，暂不发送分析")
        mime = mimetypes.guess_type(asset_file.name)[0] or "image/png"
        data_url = "data:" + mime + ";base64," + base64.b64encode(raw).decode("ascii")
        user_content.append({"type": "image_url", "image_url": {"url": data_url, "detail": "high"}})
    elif ext in {".mp4", ".mov", ".m4v", ".webm", ".avi"}:
        user_content.append({"type": "text", "text": "关联文件是视频：" + asset_file.name + "。当前版本不会抽帧，不能判断视频画面内容。"})
    else:
        raise ValueError("关联素材暂只支持图片或视频")
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
                 "User-Agent": "Prompt-Atelier/0.5"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    content = result["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("AI 返回结果不是有效 JSON 对象")
    parsed.setdefault("title", prompt_file.stem)
    parsed.setdefault("category", "其他")
    parsed.setdefault("sku", "")
    parsed.setdefault("subcategory", "")
    parsed.setdefault("material", "")
    parsed.setdefault("color", "")
    parsed.setdefault("silhouette", "")
    parsed.setdefault("fit", "")
    parsed.setdefault("details", [])
    parsed.setdefault("scene", "")
    parsed.setdefault("constraints", [])
    parsed.setdefault("tags", [])
    parsed.setdefault("prompt_summary", "")
    parsed.setdefault("prompt_text", text)
    parsed.setdefault("asset_observations", "未提供")
    parsed.setdefault("risk_notes", [])
    parsed.setdefault("confidence", 0.0)
    parsed["model"] = model.strip()
    parsed["asset_path"] = str(asset_file)
    parsed["prompt_path"] = str(prompt_file)
    return parsed
