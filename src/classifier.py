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
    # gemma 在 free tier 分類 ~125 檔約需 8 分鐘，timeout 設 600s 留緩衝；不重試避免時間翻倍。
    # 分類失敗（含 timeout）由 pipeline 以 try/except 兜底，僅少題材、不中斷。
    return OpenAI(
        api_key=settings.nvidia_api_key,
        base_url=settings.nvidia_base_url,
        timeout=600.0,
        max_retries=0,
    )


def classify_themes(stocks: list[dict]) -> dict:
    """stocks: [{"code","name","industry"}...] -> {"themes":[{name,reason,stocks:[{code,name}]}]}。

    LLM 只回 code（縮短輸出），名稱在此用本地對照補回。temperature 低以求可重現。
    """
    if not stocks:
        return {"themes": []}

    name_map = {s["code"]: s.get("name", s["code"]) for s in stocks}
    client = _client()
    messages = [
        {"role": "system", "content": THEME_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(stocks, ensure_ascii=False)},
    ]

    try:
        content = _call(client, messages, json_mode=True)
    except Exception:
        content = ""  # timeout/連線錯誤：直接放棄，不再花另一個長 timeout

    parsed = _safe_parse(content)
    # 只有「拿到回應但 parse 失敗」才退非 JSON 模式（部分模型不支援 response_format）
    if parsed is None and content:
        try:
            content = _call(client, messages, json_mode=False)
            parsed = _safe_parse(content)
        except Exception:
            parsed = None

    if parsed is None:
        return {"themes": [], "_raw": content}
    return {"themes": [_attach_names(t, name_map) for t in parsed.get("themes", [])]}


def _attach_names(theme: dict, name_map: dict) -> dict:
    codes = theme.get("codes") or [s.get("code") for s in theme.get("stocks", [])]
    return {
        "name": theme.get("name", "未命名"),
        "reason": theme.get("reason", ""),
        "stocks": [{"code": c, "name": name_map.get(c, c)} for c in codes if c],
    }


def _call(client: OpenAI, messages: list[dict], json_mode: bool) -> str:
    kwargs = {"model": settings.nvidia_model, "messages": messages, "temperature": 0.2}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(**kwargs)
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
