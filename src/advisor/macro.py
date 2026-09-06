"""FRED 總經三態：服務 0050／VOO 操作建議，不進選股／曝險公式。

門檻寫在本檔常數，不進 config.py（避免被誤當成要跑曝險回測）。
Sahm 0.50 是公開規則；HY 450/600 是信用利差慣例。
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

CACHE_DIR = Path(__file__).parent / "cache"
FRED_OBS_URL = "https://api.stlouisfed.org/fred/series/observations"

# FRED series_id
SERIES_SAHM = "SAHMREALTIME"
SERIES_CURVE = "T10Y2Y"
SERIES_HY = "BAMLH0A0HYM2"
SERIES_FED = "FEDFUNDS"

# 門檻（操作判斷，上線前由使用者拍板）
SAHM_RECESSION = 0.50
HY_STRESS = 450.0
HY_CRISIS = 600.0
HY_WIDEN_3M = 80.0
CURVE_INVERT_DAYS = 5
FED_HIKE_MONTHS = 6
FED_HIKE_EPS = 0.01

FETCH_LIMITS = {
    SERIES_SAHM: 12,
    SERIES_CURVE: 20,
    SERIES_HY: 100,
    SERIES_FED: 12,
}

REGIME_FAVORABLE = "favorable"
REGIME_DETERIORATING = "deteriorating"
REGIME_UNFAVORABLE = "unfavorable"

REGIME_ZH = {
    REGIME_FAVORABLE: "好",
    REGIME_DETERIORATING: "警戒",
    REGIME_UNFAVORABLE: "差",
}

ACTION_DCA = "dca"
ACTION_BUY_DIP = "buy_dip"
ACTION_HOLD_DCA = "hold_dca"
ACTION_WAIT = "wait"
ACTION_TRIM = "trim"
ACTION_CASH = "cash"

ACTION_TITLE = {
    ACTION_DCA: "定期定額",
    ACTION_BUY_DIP: "越跌越買",
    ACTION_HOLD_DCA: "維持定期定額",
    ACTION_WAIT: "暫停加碼",
    ACTION_TRIM: "高點適度停利",
    ACTION_CASH: "現金待命",
}

ACTION_COPY = {
    ACTION_DCA: "定期定額，不追高單筆。",
    ACTION_BUY_DIP: "越跌越買，可加碼。",
    ACTION_HOLD_DCA: "維持定期定額，暫停單筆加碼。",
    ACTION_WAIT: "先不加碼，現金待命。",
    ACTION_TRIM: "高點適度停利，暫停單筆加碼。",
    ACTION_CASH: "現金先不要加回去。",
}

SIGNAL_ZH = {"green": "綠燈", "yellow": "黃燈", "red": "紅燈"}

# 紅燈 + 這些動作 = 與「破線停損」相反，文案必須覆蓋
OVERRIDE_STOP_ACTIONS = {ACTION_BUY_DIP, ACTION_WAIT, ACTION_DCA, ACTION_HOLD_DCA}

# 核心部位：同一顆總經燈，各看自己的價燈做 2×2。SMH 不在此列。
MACRO_CORE_ETFS = ("0050.TW", "VOO")
MACRO_LABELS = {"0050.TW": "0050", "VOO": "VOO"}


def _parse_obs_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").date()


def parse_observations(raw: list[dict]) -> list[tuple[date, float]]:
    """解析 FRED observations，略過 value='.' 的缺值，依日期升冪。"""
    out: list[tuple[date, float]] = []
    for row in raw:
        val = row.get("value")
        if val is None or val == ".":
            continue
        try:
            out.append((_parse_obs_date(row["date"]), float(val)))
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda x: x[0])
    return out


def parse_pairs(pairs: list[tuple[str | date, float]]) -> list[tuple[date, float]]:
    """測試用：接受 (date|str, value) 列表。"""
    out: list[tuple[date, float]] = []
    for d, v in pairs:
        if isinstance(d, str):
            d = _parse_obs_date(d)
        out.append((d, float(v)))
    out.sort(key=lambda x: x[0])
    return out


def _latest(obs: list[tuple[date, float]]) -> tuple[date, float] | None:
    return obs[-1] if obs else None


def _value_near(obs: list[tuple[date, float]], target: date) -> float | None:
    """找日期最接近 target 的觀測值。"""
    if not obs:
        return None
    best_d, best_v = min(obs, key=lambda x: abs((x[0] - target).days))
    return best_v


def classify_legs(series: dict[str, list[tuple[date, float]]]) -> dict[str, Any]:
    """各腿狀態。缺序列時該腿 status=missing，不觸發該條件。"""
    legs: dict[str, Any] = {}

    sahm = series.get("sahm") or []
    latest_sahm = _latest(sahm)
    if latest_sahm:
        d, v = latest_sahm
        legs["sahm"] = {
            "value": round(v, 2),
            "date": d.isoformat(),
            "triggered": v >= SAHM_RECESSION,
            "status": "bad" if v >= SAHM_RECESSION else "ok",
        }
    else:
        legs["sahm"] = {"status": "missing"}

    curve = series.get("curve") or []
    latest_curve = _latest(curve)
    if latest_curve and len(curve) >= CURVE_INVERT_DAYS:
        last_n = curve[-CURVE_INVERT_DAYS:]
        inverted = all(v < 0 for _, v in last_n)
        d, v = latest_curve
        legs["curve"] = {
            "value": round(v, 2),
            "date": d.isoformat(),
            "inverted": inverted,
            "status": "watch" if inverted else "ok",
        }
    elif latest_curve:
        d, v = latest_curve
        legs["curve"] = {
            "value": round(v, 2),
            "date": d.isoformat(),
            "inverted": False,
            "status": "ok",
            "note": f"不足 {CURVE_INVERT_DAYS} 日，不判倒掛",
        }
    else:
        legs["curve"] = {"status": "missing"}

    hy = series.get("hy") or []
    latest_hy = _latest(hy)
    if latest_hy:
        d, v = latest_hy
        old = _value_near(hy, d - timedelta(days=90))
        widen = None if old is None else v - old
        if v >= HY_CRISIS:
            status = "bad"
        elif v >= HY_STRESS or (widen is not None and widen > HY_WIDEN_3M):
            status = "watch"
        else:
            status = "ok"
        legs["hy"] = {
            "value": round(v, 1),
            "date": d.isoformat(),
            "widen_3m": None if widen is None else round(widen, 1),
            "status": status,
        }
    else:
        legs["hy"] = {"status": "missing"}

    fed = series.get("fedfunds") or []
    latest_fed = _latest(fed)
    if latest_fed:
        d, v = latest_fed
        old = _value_near(fed, d - timedelta(days=30 * FED_HIKE_MONTHS))
        hiking = old is not None and v > old + FED_HIKE_EPS
        legs["fedfunds"] = {
            "value": round(v, 2),
            "date": d.isoformat(),
            "hiking_6m": hiking,
            "status": "watch" if hiking else "ok",
        }
    else:
        legs["fedfunds"] = {"status": "missing"}

    return legs


def classify_regime(legs: dict[str, Any]) -> str:
    """取最差：差 > 警戒 > 好。Fed 升息只把好降成警戒，不單獨判差。"""
    sahm = legs.get("sahm") or {}
    hy = legs.get("hy") or {}
    curve = legs.get("curve") or {}
    fed = legs.get("fedfunds") or {}

    hy_val = hy.get("value")
    widen = hy.get("widen_3m")

    if sahm.get("triggered") or (hy_val is not None and hy_val >= HY_CRISIS):
        return REGIME_UNFAVORABLE

    inverted = bool(curve.get("inverted"))
    hy_watch = hy.get("status") == "watch"
    hiking = bool(fed.get("hiking_6m"))
    if inverted or hy_watch or hiking:
        return REGIME_DETERIORATING

    return REGIME_FAVORABLE


def classify_action(regime: str, signal: str) -> str:
    """總經三態 × 核心 ETF 綠／非綠。"""
    dip = signal in ("yellow", "red")
    if regime == REGIME_FAVORABLE:
        return ACTION_BUY_DIP if dip else ACTION_DCA
    if regime == REGIME_DETERIORATING:
        return ACTION_WAIT if dip else ACTION_HOLD_DCA
    return ACTION_CASH if dip else ACTION_TRIM


def _reasons(legs: dict[str, Any]) -> list[str]:
    """可解釋的觸發原因 + 最新值。"""
    notes: list[str] = []
    sahm = legs.get("sahm") or {}
    if sahm.get("status") != "missing" and sahm.get("value") is not None:
        tag = "觸發" if sahm.get("triggered") else ""
        notes.append(f"Sahm {sahm['value']:.2f}{tag}")
    curve = legs.get("curve") or {}
    if curve.get("status") != "missing" and curve.get("value") is not None:
        tag = "倒掛" if curve.get("inverted") else ""
        notes.append(f"2s10s {curve['value']:.2f}%{tag}")
    hy = legs.get("hy") or {}
    if hy.get("status") != "missing" and hy.get("value") is not None:
        extra = ""
        if hy.get("status") == "bad":
            extra = "危機"
        elif hy.get("status") == "watch":
            extra = "轉差"
        notes.append(f"HY OAS {hy['value']:.0f}bp{extra}")
    fed = legs.get("fedfunds") or {}
    if fed.get("status") != "missing" and fed.get("value") is not None:
        tag = "升息中" if fed.get("hiking_6m") else ""
        notes.append(f"Fed {fed['value']:.2f}%{tag}")
    return notes or ["資料不足"]


def evaluate_regime(series: dict[str, list[tuple[date, float]]]) -> dict[str, Any]:
    """純函式：只算三態，不含單一 ETF 動作。FRED 只抓一次再分給各標的。"""
    legs = classify_legs(series)
    regime = classify_regime(legs)
    return {
        "regime": regime,
        "regime_zh": REGIME_ZH[regime],
        "legs": legs,
        "reasons": _reasons(legs),
    }


def evaluate(series: dict[str, list[tuple[date, float]]], etf_signal: str) -> dict[str, Any]:
    """純函式：序列 + 單一燈號 → 總經結果。測試注入 fixture 走這裡。"""
    result = evaluate_regime(series)
    result["action"] = classify_action(result["regime"], etf_signal)
    result["signal"] = etf_signal
    return result


def format_alert(result: dict[str, Any], old_macro: dict[str, Any],
                 code: str = "0050.TW") -> str:
    """LINE 用：動作句在前；紅燈且非停損動作時覆蓋破線文案。"""
    regime = result["regime"]
    action = result["action"]
    signal = result["signal"]
    reasons = "、".join(result.get("reasons") or [])
    old_r = old_macro.get("regime")
    regime_zh = REGIME_ZH[regime]
    old_zh = REGIME_ZH.get(old_r, "")
    label = MACRO_LABELS.get(code, code)

    if old_r and old_r != regime:
        head = f"總經由{old_zh}轉{regime_zh}"
    else:
        head = f"總經{regime_zh}"

    light = SIGNAL_ZH.get(signal, signal)
    lines = [
        f"📊【{label}｜{ACTION_TITLE[action]}】{head}（{reasons}）。"
        f"{light}：{ACTION_COPY[action]}"
    ]
    if signal == "red" and action in OVERRIDE_STOP_ACTIONS:
        lines.append(f"此則覆蓋 {label} 停損建議。")
    return "\n".join(lines)


def _cache_path(series_id: str) -> Path:
    return CACHE_DIR / f"fred_{series_id}.json"


def _read_cache(series_id: str, today: date) -> list[dict] | None:
    path = _cache_path(series_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("fetched_on") != today.isoformat():
        return None
    obs = payload.get("observations")
    return obs if isinstance(obs, list) else None


def _write_cache(series_id: str, today: date, observations: list[dict]) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    path = _cache_path(series_id)
    path.write_text(
        json.dumps(
            {"fetched_on": today.isoformat(), "observations": observations},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def fetch_series(series_id: str, api_key: str, today: date | None = None,
                 session: requests.Session | None = None) -> list[tuple[date, float]]:
    """抓單序列；當日快取命中則不連網。"""
    today = today or date.today()
    cached = _read_cache(series_id, today)
    if cached is not None:
        return _finalize_obs(series_id, parse_observations(cached))

    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": FETCH_LIMITS.get(series_id, 20),
    }
    http = session or requests
    resp = http.get(FRED_OBS_URL, params=params, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    observations = body.get("observations") or []
    _write_cache(series_id, today, observations)
    return _finalize_obs(series_id, parse_observations(observations))


def _finalize_obs(series_id: str, parsed: list[tuple[date, float]]) -> list[tuple[date, float]]:
    """快取存 FRED 原文；HY 百分點換成 bp 再進分類。"""
    if series_id == SERIES_HY:
        return [(d, v * 100.0) for d, v in parsed]
    return parsed


def fetch_all(api_key: str, today: date | None = None) -> dict[str, list[tuple[date, float]]]:
    """四條序列；單條失敗不拖垮其餘。"""
    today = today or date.today()
    mapping = {
        "sahm": SERIES_SAHM,
        "curve": SERIES_CURVE,
        "hy": SERIES_HY,
        "fedfunds": SERIES_FED,
    }
    out: dict[str, list[tuple[date, float]]] = {}
    with requests.Session() as session:
        for key, series_id in mapping.items():
            try:
                out[key] = fetch_series(series_id, api_key, today=today, session=session)
            except Exception as e:
                print(f"  [!] FRED {series_id} 取數失敗: {e}")
                out[key] = []
    return out


def fred_api_key() -> str:
    return os.getenv("FRED_API_KEY", "").strip()


def load_regime(api_key: str | None = None,
                today: date | None = None) -> dict[str, Any] | None:
    """抓 FRED、算三態。無 key 或四條全空 → None（呼叫端跳過，日更不中斷）。"""
    key = (api_key if api_key is not None else fred_api_key())
    if not key:
        return None
    series = fetch_all(key, today=today)
    if not any(series.values()):
        print("  [!] FRED 四條序列皆空，跳過總經。")
        return None
    result = evaluate_regime(series)
    result["date"] = (today or date.today()).isoformat()
    return result


def load_macro(etf_signal: str, api_key: str | None = None,
               today: date | None = None) -> dict[str, Any] | None:
    """單一燈號完整管線（測試／相容）。生產改走 load_regime + 各 ETF classify_action。"""
    result = load_regime(api_key=api_key, today=today)
    if result is None:
        return None
    result["action"] = classify_action(result["regime"], etf_signal)
    result["signal"] = etf_signal
    return result
