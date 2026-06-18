"""大盤狀態濾網：判斷加權指數多空，動態調整入選門檻與目標水位

順勢動能策略的勝率高度依賴市場狀態——多頭時突破延續機率高、
空頭時多數突破是假突破。空頭不是「換策略」而是「提高門檻、降低部位」。

連續水位模型（exposure_series）採專業曝險管理三因子：
- 趨勢分級：指數 vs 季線/半年線 + 季線斜率，0–1 連續而非多空三分
- 市場寬度：站上季線的個股比例——指數被權值股撐住但多數股票已轉弱的
  「窄幅多頭」，寬度會先示警，這是只看指數的盲點
- 波動率目標化：實際波動超過目標時按比例縮水位（vol targeting，
  Moreira & Muir 2017 實證可改善風險調整後報酬）
"""

import numpy as np
import pandas as pd
import yfinance as yf

from . import config


def get_regime(market: str = "TW") -> dict:
    """回傳大盤狀態：label（多頭/中性/空頭）、score 門檻、說明"""
    index_symbol = "^TWII" if market == "TW" else "^GSPC"
    twii = yf.download(index_symbol, period="1y", auto_adjust=True, progress=False)
    if isinstance(twii.columns, pd.MultiIndex):
        twii.columns = twii.columns.get_level_values(0)
    c = twii["Close"].dropna()

    close = float(c.iloc[-1])
    ma60 = float(c.rolling(60).mean().iloc[-1])
    ma120 = float(c.rolling(120).mean().iloc[-1])
    ma60_prev = float(c.rolling(60).mean().iloc[-10])

    if close > ma60 > ma120 and ma60 > ma60_prev:
        label, threshold = "多頭", config.SCORE_BULL
        note = "指數站上季線與半年線且季線上揚，順勢策略勝率最佳。"
    elif close < ma120 and close < ma60:
        label, threshold = "空頭", config.SCORE_BEAR
        note = "指數跌破季線與半年線，突破假訊號比例大增——建議僅觀察、嚴控部位。"
    else:
        label, threshold = "中性", config.SCORE_NEUTRAL
        note = "指數於均線間震盪，提高門檻只留最強標的。"

    return {
        "label": label,
        "threshold": threshold,
        "note": note,
        "close": round(close, 0),
        "ma60": round(ma60, 0),
        "ma120": round(ma120, 0),
    }


# ── 連續水位模型 ─────────────────────────────────────────────

def breadth_series(close_panel: pd.DataFrame) -> pd.Series:
    """市場寬度：每日「收盤站上季線」的個股比例（0–1）"""
    ma60 = close_panel.rolling(60).mean()
    above = (close_panel > ma60).sum(axis=1)
    valid = close_panel.notna().sum(axis=1).clip(lower=1)
    return above / valid


def exposure_series(twii_close: pd.Series, breadth: pd.Series) -> pd.Series:
    """連續目標水位（0–1，5% 階梯）。

    水位 = (趨勢分級 × 0.5 + 市場寬度 × 0.5) × 波動率縮放
    回測（exposure --compare）與每日顧問共用同一條公式，避免兩套邏輯漂移。
    """
    ma60 = twii_close.rolling(60).mean()
    ma120 = twii_close.rolling(120).mean()
    # 趨勢分級：三個條件各佔權重，0–1 共 8 級，比多空三分細
    trend = ((twii_close > ma60) * 0.4
             + (ma60 > ma120) * 0.3
             + (ma60 > ma60.shift(10)) * 0.3)
    b = ((breadth.reindex(twii_close.index).ffill() - config.BREADTH_LOW)
         / (config.BREADTH_HIGH - config.BREADTH_LOW)).clip(0, 1)
    base = config.EXP_W_TREND * trend + config.EXP_W_BREADTH * b
    # 波動率目標化：年化波動超標時等比例縮水位
    realized = twii_close.pct_change().rolling(20).std() * np.sqrt(252)
    vol_scale = (config.VOL_TARGET / realized).clip(upper=1.0)
    expo = (base * vol_scale).clip(0, 1)
    return (expo / config.EXP_STEP).round() * config.EXP_STEP


def get_exposure_live(close_panel: pd.DataFrame) -> dict:
    """今日的連續目標水位與三因子拆解（顧問/UI 顯示用）"""
    twii = yf.download("^TWII", period="1y", auto_adjust=True, progress=False)
    if isinstance(twii.columns, pd.MultiIndex):
        twii.columns = twii.columns.get_level_values(0)
    c = twii["Close"].dropna()

    breadth = breadth_series(close_panel)
    expo = exposure_series(c, breadth)

    ma60 = c.rolling(60).mean()
    ma120 = c.rolling(120).mean()
    trend = float((c.iloc[-1] > ma60.iloc[-1]) * 0.4
                  + (ma60.iloc[-1] > ma120.iloc[-1]) * 0.3
                  + (ma60.iloc[-1] > ma60.iloc[-11]) * 0.3)
    realized = float(c.pct_change().rolling(20).std().iloc[-1] * np.sqrt(252))
    return {
        "exposure": float(expo.iloc[-1]),
        "trend": round(trend, 2),
        "breadth": round(float(breadth.iloc[-1]) * 100, 1),
        "realized_vol": round(realized * 100, 1),
        "vol_scale": round(min(1.0, config.VOL_TARGET / realized), 2),
    }
