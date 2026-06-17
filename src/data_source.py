"""資料來源（免費等級設計）：

- 漲幅排行：TWSE 官方 OpenAPI 全市場當日行情（免費、免 auth、一次拿全上市）。
- 均線歷史：FinMind 單股查詢（免費需帶 data_id），只對 top-N 抓，parquet 逐檔快取。
- 基本資料（產業別）：FinMind TaiwanStockInfo（免費）。

為何這樣拆：FinMind 全市場查詢需付費等級(Backer/Sponsor)，免費走不通；
TWSE OpenAPI 免費就能一次拿整個上市當日行情，最適合做排行。
"""
from __future__ import annotations

import time
from datetime import date, timedelta

import pandas as pd
import requests

from config import CACHE_DIR, settings

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
TWSE_DAILY_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
# 機房 IP（如 GitHub Actions）或缺 header 時，TWSE 偶爾回 HTML 反爬/錯誤頁。
# 明確要求 JSON + 帶 UA 可大幅降低機率。
_TWSE_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

INFO_CACHE = CACHE_DIR / "stock_info.parquet"
_SLEEP_BETWEEN_CALLS = 0.3  # FinMind 單股節流，避免觸發 rate limit


# ---------------------------------------------------------------- FinMind
def _finmind(dataset: str, **params) -> pd.DataFrame:
    payload = {"dataset": dataset, **params}
    if settings.finmind_token:
        payload["token"] = settings.finmind_token
    resp = requests.get(FINMIND_URL, params=payload, timeout=15)
    resp.raise_for_status()
    body = resp.json()
    if body.get("status") != 200:
        raise RuntimeError(f"FinMind 錯誤: {body.get('msg')} (dataset={dataset})")
    return pd.DataFrame(body.get("data", []))


def get_stock_info(refresh: bool = False) -> pd.DataFrame:
    """全部股票基本資料：stock_id / stock_name / industry_category。"""
    if INFO_CACHE.exists() and not refresh:
        return pd.read_parquet(INFO_CACHE)
    df = _finmind("TaiwanStockInfo")
    df = df[["industry_category", "stock_id", "stock_name", "type"]].drop_duplicates(
        "stock_id"
    )
    df.to_parquet(INFO_CACHE, index=False)
    return df


def get_stock_history(stock_id: str, start: date, end: date) -> pd.DataFrame:
    """單股日 K（FinMind, free + data_id），逐檔 parquet 快取 + 增量更新。

    回傳欄位：date, close（至少這兩個）。
    """
    cache_file = CACHE_DIR / f"hist_{stock_id}.parquet"
    cached = pd.DataFrame()
    if cache_file.exists():
        cached = pd.read_parquet(cache_file)
        cached["date"] = pd.to_datetime(cached["date"]).dt.date

    fetch_start = start
    if not cached.empty:
        last = cached["date"].max()
        if last >= start:
            fetch_start = last  # 重抓最後一天補盤後更新

    new = pd.DataFrame()
    if fetch_start <= end:
        try:
            new = _finmind(
                "TaiwanStockPrice",
                data_id=stock_id,
                start_date=fetch_start.isoformat(),
                end_date=end.isoformat(),
            )
            time.sleep(_SLEEP_BETWEEN_CALLS)
            if not new.empty:
                new["date"] = pd.to_datetime(new["date"]).dt.date
        except Exception:
            # rate limit / 暫時性錯誤：有快取就降級沿用，沒有才往上拋
            if cached.empty:
                raise

    frames = [f for f in (cached, new) if not f.empty]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True).drop_duplicates("date").sort_values("date")
    df.to_parquet(cache_file, index=False)
    return df[(df["date"] >= start) & (df["date"] <= end)].reset_index(drop=True)


# ---------------------------------------------------------------- TWSE
def _roc_to_date(roc: str) -> date:
    """民國日期字串 '1150616' -> 2026-06-16。"""
    s = str(roc)
    return date(int(s[:-4]) + 1911, int(s[-4:-2]), int(s[-2:]))


def get_market_today() -> pd.DataFrame:
    """TWSE 全上市當日行情，計算漲幅%。只保留 4 位數字代號的普通股（排除 ETF/權證）。

    Change 欄為漲跌「價」，昨收 = close - change，漲幅% = change / 昨收 * 100。
    """
    rows = None
    last_body = ""
    # TWSE 回 HTML 多為間歇性（機房 IP 反爬/暫時異常），重試幾次即可
    for attempt in range(4):
        resp = requests.get(TWSE_DAILY_URL, headers=_TWSE_HEADERS, timeout=30)
        resp.raise_for_status()
        try:
            rows = resp.json()
            break
        except ValueError:
            last_body = resp.text[:80]
            if attempt < 3:
                time.sleep(2 * (attempt + 1))  # 2s, 4s, 6s 退避
    if rows is None:
        raise RuntimeError(
            "TWSE 連續回傳非 JSON（機房 IP 反爬或來源暫時異常；"
            "盤後資料尚未產生也可能如此，建議 14:30 後再跑）。"
            f" 末次 status={resp.status_code}, body前80字={last_body!r}"
        )
    if not rows:
        raise RuntimeError("TWSE 當日行情為空（可能非交易日，或盤後資料尚未產生，建議盤後 14:30 之後再跑）")
    df = pd.DataFrame(rows)
    df = df.rename(
        columns={
            "Code": "stock_id",
            "Name": "stock_name",
            "ClosingPrice": "close",
            "Change": "change",
            "TradeVolume": "volume",
            "Date": "roc_date",
        }
    )
    df = df[df["stock_id"].str.fullmatch(r"\d{4}")].copy()  # 普通股
    for c in ("close", "change", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df["date"] = df["roc_date"].map(_roc_to_date)
    df = df[(df["close"] > 0) & df["change"].notna()]
    prev_close = df["close"] - df["change"]
    df = df[prev_close > 0].copy()
    df["change_pct"] = (df["change"] / (df["close"] - df["change"]) * 100).round(2)

    return df[
        ["stock_id", "stock_name", "date", "close", "change", "change_pct", "volume"]
    ].reset_index(drop=True)
