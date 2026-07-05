# -*- coding: utf-8 -*-
"""融券 + 期貨籌碼數據抓取層

融券資料來源：台灣證交所 / 櫃買中心
期貨籌碼來源：期交所
"""

import datetime as dt
import json
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (stock-selector)"}
CACHE_DIR = Path(__file__).parent / "cache"

# ── 融資融券 ──────────────────────────────────────────────────
# 台灣證交所「融資融券統計」API (上市)
TWSE_MARGIN = "https://www.twse.com.tw/rwd/zh/fund/BFI82U"

# 櫃買中心融資融券 (上櫃)
TPEX_MARGIN = "https://www.tpex.org.tw/web/stock/margin_trading/daily"

# ── 期交所期貨籌碼 ──────────────────────────────────────────
# 期交所 API (台指期 TXF、小台 MXF)
TAIFEX_FUTURES = "https://www.taifex.com.tw/api/v1/fut/institution"


def fetch_margin_loan(day: dt.date = None, market: str = "TW") -> Optional[pd.DataFrame]:
    """取得某日融資融券統計（上市或上櫃）

    Args:
        day: 交易日期 (default: 今天)
        market: "TW" (上市) or "OTC" (上櫃)

    Returns:
        DataFrame: code, margin_buy, margin_sell, short_sell, short_cover, ...
        or None if fail
    """
    if day is None:
        day = dt.date.today()

    url = TWSE_MARGIN if market == "TW" else TPEX_MARGIN
    params = {
        "response": "json",
        "date": day.strftime("%Y%m%d")
    }

    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        # TWSE 格式
        if market == "TW":
            records = []
            for row in data.get("data", []):
                code = row[0].strip()
                if len(code) < 4 or not code.isalnum():
                    continue
                records.append({
                    "code": code,
                    "margin_buy": _to_num(row[2]),      # 融資買進
                    "margin_sell": _to_num(row[3]),     # 融資賣出
                    "margin_balance": _to_num(row[4]),  # 融資餘額
                    "short_sell": _to_num(row[6]),      # 融券賣出
                    "short_cover": _to_num(row[7]),     # 融券買進
                    "short_balance": _to_num(row[8]),   # 融券餘額
                })
            return pd.DataFrame(records)

        # TPEX 格式（如需要，待補）
        return None

    except Exception as e:
        print(f"[-] 融券資料抓取失敗 ({market}, {day}): {e}")
        return None


def fetch_taifex_institution(product: str = "TXF", day: dt.date = None) -> Optional[dict]:
    """取得期交所台指期籌碼（法人多空）

    Args:
        product: "TXF" (台指期) or "MXF" (小台期)
        day: 交易日期 (default: 最近交易日)

    Returns:
        dict: {
            "date": "...",
            "products": {
                "TXF": {
                    "trust_long": 123456,  # 投信多單
                    "trust_short": 45678,  # 投信空單
                    "dealer_long": ...,
                    "dealer_short": ...,
                    "foreign_long": ...,
                    "foreign_short": ...,
                    ...
                }
            }
        }
        or None if fail
    """
    if day is None:
        day = dt.date.today()

    params = {
        "queryDate": day.strftime("%Y/%m/%d"),
        "product": product
    }

    try:
        resp = requests.get(TAIFEX_FUTURES, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        if data.get("success"):
            records = data.get("data", {})
            return {
                "date": day.isoformat(),
                "product": product,
                "data": records
            }
        return None

    except Exception as e:
        print(f"[-] 期交所籌碼抓取失敗 ({product}, {day}): {e}")
        return None


def _to_num(s) -> float:
    """字串轉數字"""
    try:
        return float(str(s).replace(",", "").strip() or "0")
    except (ValueError, TypeError, AttributeError):
        return 0.0


# ── 快取層 ──────────────────────────────────────────────────

def _margin_cache_path(day: dt.date, market: str = "TW") -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"margin_{market}_{day.isoformat()}.pkl"


def _futures_cache_path(day: dt.date, product: str = "TXF") -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"futures_{product}_{day.isoformat()}.pkl"


def fetch_margin_cached(day: dt.date, market: str = "TW", force: bool = False) -> Optional[pd.DataFrame]:
    """融券資料（含快取）"""
    cache_path = _margin_cache_path(day, market)
    if cache_path.exists() and not force:
        try:
            return pd.read_pickle(cache_path)
        except Exception:
            pass

    df = fetch_margin_loan(day, market)
    if df is not None and not df.empty:
        df.to_pickle(cache_path)
    return df


def fetch_futures_cached(day: dt.date, product: str = "TXF", force: bool = False) -> Optional[dict]:
    """期貨籌碼（含快取）"""
    cache_path = _futures_cache_path(day, product)
    if cache_path.exists() and not force:
        try:
            with open(cache_path, "rb") as f:
                import pickle
                return pickle.load(f)
        except Exception:
            pass

    data = fetch_taifex_institution(product, day)
    if data is not None:
        with open(cache_path, "wb") as f:
            import pickle
            pickle.dump(data, f)
    return data
