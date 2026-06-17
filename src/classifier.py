"""用 NVIDIA NIM（OpenAI 相容 API）將個股分到投資題材族群。

與 nvidia-tg-bot 同一套：openai 套件 + 同一組 env。模型在雲端，本地不跑模型。
"""
from __future__ import annotations

import json

from openai import OpenAI

from config import settings
from src.prompts import THEME_SYSTEM_PROMPT


def _client() -> OpenAI:
    settings.require_nvidia()
    return OpenAI(api_key=settings.nvidia_api_key, base_url=settings.nvidia_base_url)


def classify_themes(stocks: list[dict]) -> dict:
    """stocks: [{"code","name","industry"}...] -> {"themes":[...]}。

    分類要可重現，temperature 調低；優先請求 JSON 模式，失敗則退回純解析並重試一次。
    """
    if not stocks:
        return {"themes": []}

    client = _client()
    messages = [
        {"role": "system", "content": THEME_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(stocks, ensure_ascii=False)},
    ]

    content = _call(client, messages, json_mode=True)
    parsed = _safe_parse(content)
    if parsed is None:
        # 部分模型不支援 response_format；退回一般模式重試
        content = _call(client, messages, json_mode=False)
        parsed = _safe_parse(content)

    return parsed or {"themes": [], "_raw": content}


def _call(client: OpenAI, messages: list[dict], json_mode: bool) -> str:
    kwargs = {"model": settings.nvidia_model, "messages": messages, "temperature": 0.2}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception:
        if json_mode:
            return ""  # 觸發退回路徑
        raise
    return (resp.choices[0].message.content or "").strip()


def _safe_parse(content: str) -> dict | None:
    if not content:
        return None
    text = content.strip()
    # 去除可能的 markdown 圍欄
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") :] if "{" in text else text
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "themes" in data:
            return data
    except json.JSONDecodeError:
        pass
    return None
