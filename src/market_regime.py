# -*- coding: utf-8 -*-
"""市場 Regime 偵測（多頭/混合/空頭）

用於控制全體選股策略的行為：
- 多頭（指數 > 季線 > 年線）：只執行多頭選股
- 混合（季線 > 指數 > 年線）：多空並行
- 空頭（年線 > 指數）：只執行空頭選股

用法：
    regime = get_regime(dt.date.today())  # 'bullish', 'mixed', 'bearish'
"""
import datetime as dt
from typing import Optional

import pandas as pd
import yfinance as yf

from .advisor.indicators import sma


def get_regime(asof: dt.date = None) -> str:
    """判斷市場 Regime（based on 加權指數）"""
    if asof is None:
        asof = dt.date.today()

    try:
        # 下載加權指數 2 年歷史（含所有 MA）
        df = yf.download("^TWII", period="2y", auto_adjust=True, progress=False)
        if df.empty or len(df) < 252:
            return "unknown"

        df = df.loc[:asof]
        if df.empty:
            return "unknown"

        # 處理 yfinance MultiIndex 列名（多 ticker 下載時）
        if isinstance(df.columns, pd.MultiIndex):
            close = df[("Close", "^TWII")]
        else:
            close = df["Close"]

        ma63 = sma(close, 63)
        ma252 = sma(close, 252)

        current = float(close.iloc[-1])
        ma63_val = float(ma63.iloc[-1]) if not pd.isna(ma63.iloc[-1]) else 0
        ma252_val = float(ma252.iloc[-1]) if not pd.isna(ma252.iloc[-1]) else 0

        # 判定 Regime（收盤價確認）
        if current > ma63_val > ma252_val:
            return "bullish"
        elif ma63_val > current > ma252_val:
            return "mixed"
        else:  # current <= ma252_val
            return "bearish"

    except Exception as e:
        print(f"[-] Regime 偵測失敗: {e}", flush=True)
        return "unknown"


def get_regime_description(regime: str) -> str:
    """取得 Regime 的中文描述"""
    desc = {
        "bullish": "多頭（指數 > 季線 > 年線）",
        "mixed": "混合（季線 > 指數 > 年線）",
        "bearish": "空頭（年線 > 指數）",
        "unknown": "未知",
    }
    return desc.get(regime, "未知")


def is_regime_allowed(regime: str, strategy_type: str) -> bool:
    """判斷某個策略是否在當前 Regime 下應該執行

    Args:
        regime: 'bullish', 'mixed', 'bearish'
        strategy_type: 'long', 'short', 'both'

    Returns:
        True 表示該策略應執行，False 表示應跳過
    """
    if regime == "unknown":
        return strategy_type == "long"  # 未知時預設執行多頭

    if strategy_type == "long":
        return regime in ("bullish", "mixed")
    elif strategy_type == "short":
        return regime in ("mixed", "bearish")
    elif strategy_type == "both":
        return True

    return False
