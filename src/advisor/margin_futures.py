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
# 期交所官方 OpenAPI（期貨大額交易人詳細數據）
TAIFEX_FUTURES_API = "https://openapi.taifex.com.tw/v1/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate"


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
    """取得期交所台指期籌碼（法人多空）— 用官方 OpenAPI

    Args:
        product: "TXF" (台指期) or "MXF" (小台期)
        day: 交易日期 (default: 最近交易日)

    Returns:
        dict: {
            "date": "2026-07-03",
            "product": "TXF",
            "trust_net": 12345,      # 投信淨持倉
            "dealer_net": -5678,     # 自營商淨持倉
            "foreign_net": 3456,     # 外資淨持倉
            "source": "taifex_api"
        }
        or fallback_empty if fail
    """
    if day is None:
        day = dt.date.today()

    # 官方 OpenAPI 參數
    params = {
        "startDate": day.strftime("%Y%m%d"),
        "endDate": day.strftime("%Y%m%d"),
    }

    # 契約代碼對應
    contract_map = {
        "TXF": "台指期",
        "MXF": "小台期",
    }
    contract_code = contract_map.get(product, product)

    try:
        resp = requests.get(TAIFEX_FUTURES_API, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        if not isinstance(data, list):
            data = [data]

        # 解析籌碼數據：按 Item 類別（投信、自營商、外資）累加淨持倉
        trust_net = 0
        dealer_net = 0
        foreign_net = 0

        for record in data:
            if record.get("ContractCode") != contract_code:
                continue

            item = record.get("Item", "")
            net_open_interest = int(record.get("OpenInterest(Net)", 0))

            if "投信" in item or "Investment" in item:
                trust_net += net_open_interest
            elif "自營" in item or "Dealer" in item:
                dealer_net += net_open_interest
            elif "外資" in item or "Foreign" in item:
                foreign_net += net_open_interest

        return {
            "date": day.isoformat(),
            "product": product,
            "trust_net": trust_net,
            "dealer_net": dealer_net,
            "foreign_net": foreign_net,
            "source": "taifex_api"
        }

    except Exception as e:
        print(f"[-] 期交所籌碼 API 失敗 ({product}, {day}): {e}")

    # 備選方案
    return {
        "date": day.isoformat(),
        "product": product,
        "trust_net": 0,
        "dealer_net": 0,
        "foreign_net": 0,
        "source": "fallback_empty",
    }


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
