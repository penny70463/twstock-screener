# -*- coding: utf-8 -*-
"""事件驅動選股輔助（展覽會 + 法說會日期 + 供應鏈映射）

用法：
    exh = get_exhibitions_in_window(asof, days=14)  # 往後 14 天有哪些展覽會
    supply = get_supply_chain("2330")  # 台積電的供應鏈股
"""
import datetime as dt
import json
from pathlib import Path
from typing import Optional, List, Dict

REPO = Path(__file__).resolve().parent.parent
EVENTS_FILE = REPO / "data" / "events_calendar.json"


def load_events_calendar() -> dict:
    """載入展覽會日期庫"""
    try:
        with open(EVENTS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"[-] 找不到 {EVENTS_FILE}")
        return {"exhibitions": []}


def get_exhibitions_in_window(asof: dt.date, days: int = 14) -> List[dict]:
    """取得指定日期往後 N 天內的展覽會

    Args:
        asof: 基準日期
        days: 往後查看天數

    Returns:
        [{"name_zh": "...", "lookback_days": ..., "supply_chain": {...}}, ...]
    """
    cal = load_events_calendar()
    window_end = asof + dt.timedelta(days=days)
    result = []

    for exh in cal.get("exhibitions", []):
        # 簡化版：只看月份（未來改進：精確到日期）
        season = exh.get("season")
        if isinstance(season, list):
            seasons = season
        else:
            seasons = [season]

        for s in seasons:
            if asof.month <= s <= window_end.month or (window_end.month < asof.month and s >= asof.month):
                # 簡單判斷：如果基準月 <= 目標月 <= 視窗月，就包含
                # （此邏輯需精化，但初版先用月份估計）
                result.append({
                    "name_zh": exh.get("name_zh"),
                    "name_en": exh.get("name_en"),
                    "season": s,
                    "lookback_days": exh.get("lookback_days", 10),
                    "key_stocks": exh.get("key_stocks", []),
                    "supply_chain": exh.get("supply_chain", {}),
                })

    return result


def get_supply_chain(stock_id: str) -> List[str]:
    """取得某檔股票的供應鏈股清單

    例：get_supply_chain("2330") → ["5483", "4952", "2303", ...]
    """
    cal = load_events_calendar()
    supply_chains = {}

    for exh in cal.get("exhibitions", []):
        sc = exh.get("supply_chain", {})
        for stock, chain in sc.items():
            if stock not in supply_chains:
                supply_chains[stock] = set()
            supply_chains[stock].update(chain)

    return list(supply_chains.get(stock_id, []))


def get_related_exhibitions(stock_id: str) -> List[dict]:
    """取得某檔股票相關的展覽會清單

    例：get_related_exhibitions("2330") → [COMPUTEX, SEMICON Taiwan, ...]
    """
    cal = load_events_calendar()
    result = []

    for exh in cal.get("exhibitions", []):
        if stock_id in exh.get("key_stocks", []) or stock_id in exh.get("supply_chain", {}):
            result.append({
                "name_zh": exh.get("name_zh"),
                "season": exh.get("season"),
                "lookback_days": exh.get("lookback_days", 10),
            })

    return result
