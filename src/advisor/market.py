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

# 單日缺 K（Yahoo 週末/時區洞）會讓 rolling(60) 的 MA 整列變 NaN，
# (close > NaN) 全算 False，寬度從 ~55% 假摔成個位數（2026-09-01 美股 5.2%）。
BREADTH_MA_WINDOW = 60
BREADTH_MA_MIN_PERIODS = 50
BREADTH_FFILL_LIMIT = 2
BREADTH_COVERAGE_WARN = 0.80


def _breadth_inputs(close_panel: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """前向填最多 2 日缺口，MA 允許 50/60 有效列，避免單日缺 K 打穿寬度。"""
    filled = close_panel.ffill(limit=BREADTH_FFILL_LIMIT)
    ma60 = filled.rolling(BREADTH_MA_WINDOW, min_periods=BREADTH_MA_MIN_PERIODS).mean()
    return filled, ma60


def breadth_series(close_panel: pd.DataFrame) -> pd.Series:
    """市場寬度：每日「收盤站上季線」的個股比例（0–1）。

    分母只計「收盤與 MA60 都有效」的股票，避免 MA 為 NaN 時被算成沒站上。
    """
    filled, ma60 = _breadth_inputs(close_panel)
    comparable = filled.notna() & ma60.notna()
    above = ((filled > ma60) & comparable).sum(axis=1)
    valid = comparable.sum(axis=1).clip(lower=1)
    return above / valid


def breadth_coverage(close_panel: pd.DataFrame) -> float:
    """最後一日有有效 MA60 的股票佔比（0–1），供日誌與失真示警。"""
    if close_panel is None or close_panel.empty:
        return 0.0
    filled, ma60 = _breadth_inputs(close_panel)
    comparable = filled.notna() & ma60.notna()
    n = comparable.shape[1]
    if n == 0:
        return 0.0
    return float(comparable.iloc[-1].sum() / n)


def _vol_target(market: str = "TW") -> float:
    """依市場回傳對應的固定目標波動率。"""
    return config.VOL_TARGET_US if market == "US" else config.VOL_TARGET_TW


def vol_target_series(realized: pd.Series, market: str = "TW",
                      adaptive: bool | None = None) -> pd.Series:
    """目標波動率序列：固定常數或滾動一年分位數（依 VOL_TARGET_ADAPTIVE）。

    自適應模式下，目標 = 近一年 20 日實現波動率的分位數，夾在 floor/cap 之間；
    語義從「絕對波動目標」變成「短期波動高於自身一年常態才縮水位」，
    修正固定目標在高波動制度下的長期欠配。資料不足一年時退回固定目標。
    """
    fixed = _vol_target(market)
    if adaptive is None:
        adaptive = config.VOL_TARGET_ADAPTIVE
    if not adaptive:
        return pd.Series(fixed, index=realized.index)
    if market == "US":
        floor, cap = config.VOL_TARGET_FLOOR_US, config.VOL_TARGET_CAP_US
    else:
        floor, cap = config.VOL_TARGET_FLOOR_TW, config.VOL_TARGET_CAP_TW
    tgt = realized.rolling(config.VOL_TARGET_WINDOW, min_periods=126).quantile(
        config.VOL_TARGET_QUANTILE)
    return tgt.clip(floor, cap).fillna(fixed)


def exposure_series(twii_close: pd.Series, breadth: pd.Series,
                    market: str = "TW") -> pd.Series:
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
    # 波動率目標化：年化波動超標時等比例縮水位（目標可為固定或滾動分位數）
    realized = twii_close.pct_change().rolling(20).std() * np.sqrt(252)
    vol_target = vol_target_series(realized, market)
    vol_scale = (vol_target / realized).clip(upper=1.0)
    expo = (base * vol_scale).clip(0, 1)
    return (expo / config.EXP_STEP).round() * config.EXP_STEP


def get_exposure_live(close_panel: pd.DataFrame, market: str = "TW") -> dict:
    """今日的連續目標水位與三因子拆解（顧問/UI 顯示用）"""
    index_symbol = "^TWII" if market == "TW" else "^GSPC"

    # 2 年：自適應目標需要一年以上的實現波動率歷史（rolling 252）
    idx = yf.download(index_symbol, period="2y", auto_adjust=True, progress=False)
    if isinstance(idx.columns, pd.MultiIndex):
        idx.columns = idx.columns.get_level_values(0)
    c = idx["Close"].dropna()

    coverage = breadth_coverage(close_panel)
    if coverage < BREADTH_COVERAGE_WARN:
        print(f"  ! 寬度樣本不足：最後一日僅 {coverage:.0%} 檔 MA60 有效，"
              f"請檢查歷史缺 K（水位可能仍失真）", flush=True)

    breadth = breadth_series(close_panel)
    expo = exposure_series(c, breadth, market=market)

    ma60 = c.rolling(60).mean()
    ma120 = c.rolling(120).mean()
    trend = float((c.iloc[-1] > ma60.iloc[-1]) * 0.4
                  + (ma60.iloc[-1] > ma120.iloc[-1]) * 0.3
                  + (ma60.iloc[-1] > ma60.iloc[-11]) * 0.3)
    realized_s = c.pct_change().rolling(20).std() * np.sqrt(252)
    realized = float(realized_s.iloc[-1])
    vol_target = float(vol_target_series(realized_s, market).iloc[-1])
    return {
        "exposure": float(expo.iloc[-1]),
        "trend": round(trend, 2),
        "breadth": round(float(breadth.iloc[-1]) * 100, 1),
        "realized_vol": round(realized * 100, 1),
        "vol_target": round(vol_target * 100, 1),
        "vol_scale": round(min(1.0, vol_target / realized), 2),
        "breadth_coverage": round(coverage * 100, 1),
    }
