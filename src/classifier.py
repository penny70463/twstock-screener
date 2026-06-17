"""用 NVIDIA NIM（OpenAI 相容 API）將個股分到投資題材族群。

與 nvidia-tg-bot 同一套：openai 套件 + 同一組 env。模型在雲端，本地不跑模型。
"""
from __future__ import annotations

import json
from pathlib import Path

from openai import OpenAI

from config import RESULT_DIR, settings
from src.prompts import THEME_SYSTEM_PROMPT


def _client() -> OpenAI:
    settings.require_nvidia()
    # gemma 是唯一能產出「真題材」的模型（其他模型快但亂分），代價是慢：free tier
    # 分類 ~125 檔約 8-10 分鐘且有波動，timeout 設 900s 留緩衝；不重試避免時間翻倍。
    # 偶爾仍可能超時，由 pipeline 以 try/except 兜底，僅少題材、不中斷。
    return OpenAI(
        api_key=settings.nvidia_api_key,
        base_url=settings.nvidia_base_url,
        timeout=900.0,
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
    except Exception as e:  # timeout/連線錯誤：直接放棄，不再花另一個長 timeout
        content = ""
        print(f"  ! LLM 呼叫失敗: {e}", flush=True)

    parsed = _safe_parse(content)
    # 只有「拿到回應但 parse 失敗」才退非 JSON 模式（部分模型不支援 response_format）
    if parsed is None and content:
        try:
            content = _call(client, messages, json_mode=False)
            parsed = _safe_parse(content)
        except Exception:
            parsed = None

    if parsed is None:
        # 留證據：parse 失敗時把原始回應落地，才能事後查是截斷還是格式跑掉
        _dump_raw(content)
        return {"themes": [], "_raw": content}
    return {"themes": [_attach_names(t, name_map) for t in parsed.get("themes", [])]}


def _dump_raw(content: str) -> None:
    try:
        p = Path(RESULT_DIR) / "_llm_raw_failed.txt"
        p.write_text(content or "(空回應)", encoding="utf-8")
        print(f"  ! 題材 parse 失敗，原始回應已存 {p}（長度 {len(content)}）", flush=True)
    except Exception:
        pass


def _attach_names(theme: dict, name_map: dict) -> dict:
    codes = theme.get("codes") or [s.get("code") for s in theme.get("stocks", [])]
    # 只保留輸入清單內的代號：LLM 偶爾 hallucinate 代號（如 2300→23008），直接濾掉
    return {
        "name": theme.get("name", "未命名"),
        "reason": theme.get("reason", ""),
        "stocks": [{"code": c, "name": name_map[c]} for c in codes if c in name_map],
    }


def _call(client: OpenAI, messages: list[dict], json_mode: bool) -> str:
    kwargs = {
        "model": settings.nvidia_model,
        "messages": messages,
        "temperature": 0.2,
        # 125 檔分類完整輸出可達 ~3000 字，預設 max_tokens 偏小會截斷 → JSON 壞掉 → 0 題材
        "max_tokens": 4096,
    }
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
    # 截斷救援：JSON 被切斷時，逐一抽出已完整的 theme 物件，不要整批丟掉
    salvaged = _salvage_themes(text)
    if salvaged:
        print(f"  ! JSON 不完整，救回 {len(salvaged)} 個完整題材", flush=True)
        return {"themes": salvaged}
    return None


def _salvage_themes(text: str) -> list[dict]:
    """從可能被截斷的字串中，掃出每個完整的 {..."codes":[...]} theme 物件。"""
    themes: list[dict] = []
    i = 0
    while True:
        j = text.find('"name"', i)
        if j == -1:
            break
        obj_start = text.rfind("{", 0, j)
        if obj_start == -1:
            i = j + 6
            continue
        depth, k, end = 0, obj_start, -1
        in_str, esc = False, False
        while k < len(text):
            c = text[k]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        end = k
                        break
            k += 1
        if end == -1:
            break  # 物件未閉合（被截斷），停止
        try:
            obj = json.loads(text[obj_start : end + 1])
            if isinstance(obj, dict) and "name" in obj and ("codes" in obj or "stocks" in obj):
                themes.append(obj)
        except json.JSONDecodeError:
            pass
        i = end + 1
    return themes
