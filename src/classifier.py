"""用 NVIDIA NIM（OpenAI 相容 API）將個股分到投資題材族群。

與 nvidia-tg-bot 同一套：openai 套件 + 同一組 env。模型在雲端，本地不跑模型。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from openai import OpenAI

from config import RESULT_DIR, settings
from src.prompts import get_prompts


def _client() -> OpenAI:
    settings.require_nvidia()
    # gemma free tier 延遲極不穩（10s~500s+）且連跑會被限流。分批呼叫，每批設較短 timeout
    # 並不重試：被限流卡住的批次快速放棄、跳過，仍保住其他批的題材（部分結果勝過全 0）。
    # 串流讓正常批次的連線持續有資料，避免被當 idle 砍。
    return OpenAI(
        api_key=settings.nvidia_api_key,
        base_url=settings.nvidia_base_url,
        timeout=240.0,
        max_retries=0,
    )


# CI 出口到 NVIDIA 的長請求會在 ~270s 被砍（與請求長短相關）。切小批讓每次請求
# 遠低於該上限；且單批失敗只少該批題材，不會整批 0。30 檔/批 ≈ 5 批。
_BATCH_SIZE = 30


def classify_themes(stocks: list[dict], market: str = "TW") -> dict:
    """stocks: [{"code","name","industry"}...] -> {"themes":[{name,reason,stocks:[{code,name}]}]}。

    分批呼叫 LLM（避開 CI 長請求被砍），各批結果再依題材名合併。LLM 只回 code，名稱本地補回。
    market 決定用台股或美股的題材 prompt。
    """
    if not stocks:
        return {"themes": []}

    system_prompt, merge_prompt = get_prompts(market)
    name_map = {s["code"]: s.get("name", s["code"]) for s in stocks}
    client = _client()
    batches = [stocks[i : i + _BATCH_SIZE] for i in range(0, len(stocks), _BATCH_SIZE)]

    raw_themes: list[dict] = []
    for bi, batch in enumerate(batches, 1):
        if bi > 1:
            time.sleep(1.5)  # 批次間留白，降低 free tier 突發限流機率
        themes = _classify_batch(client, batch, bi, system_prompt)
        print(f"    批次 {bi}/{len(batches)}（{len(batch)} 檔）→ {len(themes)} 題材", flush=True)
        raw_themes.extend(themes)

    # 階段一：完全同名先併（codes 層級），減少要丟給階段二的量
    stage1 = _merge_exact(raw_themes)

    # 階段二：分批會把同題材切成近義名（記憶體/記憶體封測…），且各批有各自「其他」。
    #   把精簡後的題材名+codes 丟回 LLM 做一次語意歸併。輸入小、輸出小 → 快又穩。
    #   多檔（>1 批）才需要；單批沒有跨批碎片問題。
    final = stage1
    if len(batches) > 1 and stage1:
        consolidated = _consolidate(client, stage1, merge_prompt)
        if consolidated:
            print(f"    階段二歸併：{len(stage1)} → {len(consolidated)} 題材", flush=True)
            final = consolidated
        else:
            print("    ! 階段二歸併失敗，沿用階段一結果", flush=True)

    return {"themes": _attach_all(final, name_map)}


def _classify_batch(client: OpenAI, batch: list[dict], bi: int, system_prompt: str) -> list[dict]:
    """單批分類，回傳原始 theme dict 清單（含 codes，尚未補名稱）。失敗回空清單。"""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(batch, ensure_ascii=False)},
    ]
    try:
        content = _call(client, messages, json_mode=True)
    except Exception as e:
        print(f"  ! 批次 {bi} LLM 呼叫失敗: {e}", flush=True)
        return []

    parsed = _safe_parse(content)
    # 拿到回應但 parse 失敗才退非 JSON 模式（部分模型不支援 response_format）
    if parsed is None and content:
        try:
            parsed = _safe_parse(_call(client, messages, json_mode=False))
        except Exception:
            parsed = None

    if parsed is None:
        _dump_raw(content, bi)
        return []
    return [t for t in parsed.get("themes", []) if isinstance(t, dict)]


def _merge_exact(raw_themes: list[dict]) -> list[dict]:
    """完全同名合併，codes 去重。回傳 [{name,reason,codes}]（尚未補名稱、未濾 hallucinate）。"""
    order: list[str] = []
    bucket: dict[str, dict] = {}
    for t in raw_themes:
        name = (t.get("name") or "未命名").strip()
        codes = t.get("codes") or [s.get("code") for s in t.get("stocks", [])]
        if name not in bucket:
            bucket[name] = {"reason": t.get("reason", ""), "codes": []}
            order.append(name)
        bucket[name]["codes"].extend(c for c in codes if c)

    out: list[dict] = []
    for name in order:
        seen: set[str] = set()
        codes = [c for c in bucket[name]["codes"] if not (c in seen or seen.add(c))]
        out.append({"name": name, "reason": bucket[name]["reason"], "codes": codes})
    return out


_CONSOLIDATE_RETRIES = 3


def _consolidate(client: OpenAI, themes: list[dict], merge_prompt: str) -> list[dict] | None:
    """階段二：把碎片化題材丟回 LLM 做語意歸併。輸入小、重試成本低，故重試數次救連線中斷。"""
    payload = [{"name": t["name"], "codes": t["codes"]} for t in themes]
    messages = [
        {"role": "system", "content": merge_prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    last = ""
    for attempt in range(1, _CONSOLIDATE_RETRIES + 1):
        try:
            content = _call(client, messages, json_mode=True)
        except Exception as e:
            print(f"  ! 階段二第 {attempt} 次呼叫失敗: {e}", flush=True)
            time.sleep(2)
            continue
        last = content
        parsed = _safe_parse(content)
        if parsed is not None:
            merged = [t for t in parsed.get("themes", []) if isinstance(t, dict)]
            if merged:
                return merged
        print(f"  ! 階段二第 {attempt} 次 parse 失敗（長度 {len(content)}），重試", flush=True)
        time.sleep(2)
    _dump_raw(last, 0)
    return None


def _attach_all(themes: list[dict], name_map: dict) -> list[dict]:
    """補回股票名稱，並濾掉不在輸入清單內的代號（hallucinate）。"""
    out: list[dict] = []
    for t in themes:
        name = (t.get("name") or "未命名").strip()
        codes = t.get("codes") or [s.get("code") for s in t.get("stocks", [])]
        seen: set[str] = set()
        stocks = []
        for c in codes:
            if c in name_map and c not in seen:
                seen.add(c)
                stocks.append({"code": c, "name": name_map[c]})
        if stocks:
            out.append({"name": name, "reason": t.get("reason", ""), "stocks": stocks})
    return out


def _dump_raw(content: str, bi: int) -> None:
    try:
        p = Path(RESULT_DIR) / f"_llm_raw_failed_b{bi}.txt"
        p.write_text(content or "(空回應)", encoding="utf-8")
        print(f"  ! 批次 {bi} parse 失敗，原始回應已存 {p}（長度 {len(content)}）", flush=True)
    except Exception:
        pass


def _call(client: OpenAI, messages: list[dict], json_mode: bool) -> str:
    kwargs = {
        "model": settings.nvidia_model,
        "messages": messages,
        "temperature": 0.2,
        # 125 檔分類完整輸出可達 ~3000 字，預設 max_tokens 偏小會截斷 → JSON 壞掉 → 0 題材
        "max_tokens": 4096,
        # 串流：token 邊生成邊傳，連線持續有資料，避免長請求被 proxy/idle timeout 砍斷
        "stream": True,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    chunks: list[str] = []
    for ev in client.chat.completions.create(**kwargs):
        if ev.choices and ev.choices[0].delta and ev.choices[0].delta.content:
            chunks.append(ev.choices[0].delta.content)
    return "".join(chunks).strip()


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
