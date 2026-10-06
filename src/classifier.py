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
    settings.require_theme_llm()
    # 免費額度延遲不穩且連跑會被限流。分批呼叫，SDK 不重試（max_retries=0），
    # 重試由 _classify_batch 以批次為單位控制：失敗批次稍後再試一次即放棄，
    # 保住其他批的題材（部分結果勝過全 0；
    # 但股票少時只有一批，不重試會一次失敗就整天 0 題材——2026-07-14 實際發生過）。
    # 串流讓正常批次的連線持續有資料，避免被當 idle 砍。
    # Gemini 實測一批約 20 秒。timeout 收到 60 秒，避免端點又掛時每批空等 240 秒。
    if settings.theme_llm == "gemini":
        return OpenAI(
            api_key=settings.gemini_api_key,
            base_url=settings.gemini_base_url,
            timeout=60.0,
            max_retries=0,
        )
    return OpenAI(
        api_key=settings.nvidia_api_key,
        base_url=settings.nvidia_base_url,
        timeout=240.0,
        max_retries=0,
    )


# 10 檔一批：請求較短，比較不會撞上 240 秒；單批逾時也不會把整天題材清成 0。
_BATCH_SIZE = 10

# 成功分到的題材依代號留下。隔日同一檔不再打 API。
# 「其他／未分類」是收納桶，不是穩定題材，不進快取，下次仍送 LLM。
_CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / "cache" / "theme_by_code.json"
_CACHE_SKIP_THEMES = frozenset({"未分類", "未命名", "其他"})


def _load_cache() -> dict:
    try:
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_cache(cache: dict) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _partition_cached(stocks: list[dict], market_cache: dict) -> tuple[list[dict], list[dict]]:
    """快取命中且名稱沒變的留在本地，其餘送 LLM。回傳 (fresh, cached)。"""
    fresh: list[dict] = []
    cached: list[dict] = []
    for s in stocks:
        code = str(s.get("code", "")).strip()
        name = s.get("name") or code
        hit = market_cache.get(code) if isinstance(market_cache, dict) else None
        theme = hit.get("theme") if isinstance(hit, dict) else ""
        if theme and theme not in _CACHE_SKIP_THEMES and hit.get("name") == name:
            cached.append({**s, "code": code, "name": name, "theme": theme, "reason": hit.get("reason", "")})
        else:
            fresh.append({**s, "code": code, "name": name})
    return fresh, cached


def _cached_as_raw(cached: list[dict]) -> list[dict]:
    order: list[str] = []
    bucket: dict[str, dict] = {}
    for s in cached:
        theme = s["theme"]
        if theme not in bucket:
            bucket[theme] = {"reason": s.get("reason", ""), "codes": []}
            order.append(theme)
        bucket[theme]["codes"].append(s["code"])
    return [{"name": name, "reason": bucket[name]["reason"], "codes": bucket[name]["codes"]} for name in order]


def _store_cache(cache: dict, market: str, themes: list[dict]) -> None:
    """只寫入這次結果裡、有穩定題材名的代號。逾時沒出現的代號維持未快取。"""
    market_cache = cache.setdefault(market, {})
    if not isinstance(market_cache, dict):
        market_cache = {}
        cache[market] = market_cache
    for t in themes:
        theme = (t.get("name") or "").strip()
        if not theme or theme in _CACHE_SKIP_THEMES:
            continue
        reason = t.get("reason", "")
        for s in t.get("stocks") or []:
            code = str(s.get("code", "")).strip()
            if not code:
                continue
            market_cache[code] = {
                "theme": theme,
                "reason": reason,
                "name": s.get("name") or code,
            }


def classify_themes(stocks: list[dict], market: str = "TW", cache_ns: str | None = None) -> dict:
    """stocks: [{"code","name","industry"}...] -> {"themes":[{name,reason,stocks:[{code,name}]}]}。

    分批呼叫 LLM（避開 CI 長請求被砍），各批結果再依題材名合併。LLM 只回 code，名稱本地補回。
    market 決定用台股或美股的題材 prompt。
    代號已有快取且名稱未變則不送 API；這次成功分到的代號寫回快取。
    cache_ns 把每日篩選和族群突破分開。同一檔在兩份清單裡的題材不必相同。
    """
    if not stocks:
        return {"themes": [], "theme_status": "skipped"}

    ns = cache_ns or market
    cache = _load_cache()
    fresh, cached = _partition_cached(stocks, cache.get(ns) or {})
    name_map = {s["code"]: s.get("name", s["code"]) for s in fresh + cached}
    if cached:
        print(f"  題材快取命中 {len(cached)} 檔，送 LLM {len(fresh)} 檔", flush=True)
    if not fresh:
        themes = _attach_all(_cached_as_raw(cached), name_map)
        return {"themes": themes, "theme_status": "ok"}

    system_prompt, merge_prompt = get_prompts(market)
    print(f"  題材 LLM：{settings.theme_llm} / {settings.theme_model}", flush=True)
    client = _client()
    batches = [fresh[i : i + _BATCH_SIZE] for i in range(0, len(fresh), _BATCH_SIZE)]

    raw_themes: list[dict] = []
    batch_statuses: list[str] = []
    failed: list[tuple[int, list[dict]]] = []
    for bi, batch in enumerate(batches, 1):
        if bi > 1:
            time.sleep(1.5)
        themes, status = _classify_batch(client, batch, bi, system_prompt)
        print(f"    批次 {bi}/{len(batches)}（{len(batch)} 檔）→ {len(themes)} 題材", flush=True)
        if status in ("timeout", "failed"):
            failed.append((bi, batch))
            continue
        raw_themes.extend(themes)
        batch_statuses.append(status)

    # 緊接著重試多半還是逾時。等其他批跑完再打一次，成功的呼叫都是隔了一批之後。
    if failed:
        print(f"  失敗 {len(failed)} 批，稍後重試", flush=True)
        time.sleep(20)
        for bi, batch in failed:
            themes, status = _classify_batch(client, batch, bi, system_prompt)
            print(f"    重試批次 {bi}/{len(batches)}（{len(batch)} 檔）→ {len(themes)} 題材", flush=True)
            raw_themes.extend(themes)
            batch_statuses.append(status)

    # 階段一：完全同名先併（codes 層級），再併入快取裡已分過的代號。
    stage1 = _merge_exact(raw_themes + _cached_as_raw(cached))

    # 階段二：分批會把同題材切成近義名（記憶體/記憶體封測…），且各批有各自「其他」。
    #   把精簡後的題材名+codes 丟回 LLM 做一次語意歸併。輸入小、輸出小 → 快又穩。
    #   多檔（>1 批）或有快取要併進來才需要；這次全部逾時就不再打階段二。
    final = stage1
    consolidate_timeout = False
    if raw_themes and stage1 and (len(batches) > 1 or cached):
        consolidated, consolidate_timeout = _consolidate(client, stage1, merge_prompt)
        if consolidated:
            print(f"    階段二歸併：{len(stage1)} → {len(consolidated)} 題材", flush=True)
            final = consolidated
        else:
            print("    ! 階段二歸併失敗，沿用階段一結果", flush=True)

    attached = _attach_all(final, name_map)
    _store_cache(cache, ns, attached)
    _save_cache(cache)
    return {
        "themes": attached,
        "theme_status": aggregate_theme_status(batch_statuses, consolidate_timeout),
    }


_BATCH_ATTEMPTS = 1  # 第二次嘗試改在全部分批之後，不在失敗當下連打


def is_timeout(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "timeout" in msg or "timed out" in msg


def aggregate_theme_status(batch_statuses: list[str], consolidate_timeout: bool = False) -> str:
    """ok：每批都拿到模型 JSON。empty：模型成功但沒有題材。timeout：有呼叫逾時。"""
    if not batch_statuses:
        return "skipped"
    if "timeout" in batch_statuses or consolidate_timeout:
        return "timeout"
    if all(s == "empty" for s in batch_statuses):
        return "empty"
    if all(s == "ok" for s in batch_statuses):
        return "ok"
    if any(s == "failed" for s in batch_statuses):
        return "failed"
    return "ok"


def _classify_batch(client: OpenAI, batch: list[dict], bi: int, system_prompt: str) -> tuple[list[dict], str]:
    """單批分類。回傳 (theme dicts, status)。status 為 ok / empty / timeout / failed。"""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(batch, ensure_ascii=False)},
    ]
    content = None
    last_err: BaseException | None = None
    for attempt in range(1, _BATCH_ATTEMPTS + 1):
        try:
            content = _call(client, messages, json_mode=True)
            break
        except Exception as e:
            last_err = e
            print(f"  ! 批次 {bi} 第 {attempt} 次 LLM 呼叫失敗: {e}", flush=True)
            if attempt < _BATCH_ATTEMPTS:
                time.sleep(10)  # 排隊逾時後稍等再試，避開瞬間壅塞
    if content is None:
        return [], "timeout" if last_err and is_timeout(last_err) else "failed"

    parsed = _safe_parse(content)
    # 拿到回應但 parse 失敗才退非 JSON 模式（部分模型不支援 response_format）
    if parsed is None and content:
        try:
            parsed = _safe_parse(_call(client, messages, json_mode=False))
        except Exception:
            parsed = None

    if parsed is None:
        _dump_raw(content, bi)
        return [], "failed"
    themes = [t for t in parsed.get("themes", []) if isinstance(t, dict)]
    return themes, "ok" if themes else "empty"


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


_CONSOLIDATE_RETRIES = 1


def _consolidate(client: OpenAI, themes: list[dict], merge_prompt: str) -> tuple[list[dict] | None, bool]:
    """階段二：把碎片化題材丟回 LLM 做語意歸併。輸入小、重試成本低，故重試數次救連線中斷。"""
    payload = [{"name": t["name"], "codes": t["codes"]} for t in themes]
    messages = [
        {"role": "system", "content": merge_prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    last = ""
    saw_timeout = False
    for attempt in range(1, _CONSOLIDATE_RETRIES + 1):
        try:
            content = _call(client, messages, json_mode=True)
        except Exception as e:
            saw_timeout = saw_timeout or is_timeout(e)
            print(f"  ! 階段二第 {attempt} 次呼叫失敗: {e}", flush=True)
            time.sleep(2)
            continue
        last = content
        parsed = _safe_parse(content)
        if parsed is not None:
            merged = [t for t in parsed.get("themes", []) if isinstance(t, dict)]
            if merged:
                return merged, False
        print(f"  ! 階段二第 {attempt} 次 parse 失敗（長度 {len(content)}），重試", flush=True)
        time.sleep(2)
    _dump_raw(last, 0)
    return None, saw_timeout


def _attach_all(themes: list[dict], name_map: dict) -> list[dict]:
    """補回股票名稱，並濾掉不在輸入清單內的代號（hallucinate）。"""
    out: list[dict] = []
    for t in themes:
        name = (t.get("name") or "未命名").strip()
        codes = t.get("codes") or [s.get("code") for s in t.get("stocks", [])]
        seen: set[str] = set()
        stocks = []
        for c in codes:
            c_str = str(c).strip()
            if c_str in name_map and c_str not in seen:
                seen.add(c_str)
                stocks.append({"code": c_str, "name": name_map[c_str]})
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
        "model": settings.theme_model,
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
