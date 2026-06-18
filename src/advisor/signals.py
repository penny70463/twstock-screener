"""技術訊號（向量化：回傳與日線等長的布林 Series）

每個函式對「每一個交易日」都給出訊號判定，所以同一套程式碼：
- 即時選股 → 取最後一日（.iloc[-1]）
- 回測     → 取整條序列統計歷史勝率

法人籌碼只有近幾日資料，無法回測，故不在此檔（見 scoring.py）。
"""

import pandas as pd

from . import config
from .indicators import sma, rsi, macd_hist


def ma_alignment(df: pd.DataFrame) -> pd.Series:
    """均線多頭排列：MA5 > MA20 > MA60，價在 MA5 上，MA20 上揚"""
    c = df["Close"]
    ma_f, ma_m, ma_s = sma(c, config.MA_FAST), sma(c, config.MA_MID), sma(c, config.MA_SLOW)
    return (ma_f > ma_m) & (ma_m > ma_s) & (c > ma_f) & (ma_m > ma_m.shift(5))


def breakout_55(df: pd.DataFrame) -> pd.Series:
    """海龜突破：收盤創 55 日新高 + 量能 > 1.3 倍 20 日均量"""
    c, v = df["Close"], df["Volume"]
    prior_high = c.shift(1).rolling(config.BREAKOUT_WINDOW).max()
    vol_ok = v > v.rolling(20).mean() * config.BREAKOUT_VOL_RATIO
    return (c > prior_high) & vol_ok


def macd_golden_cross(df: pd.DataFrame) -> pd.Series:
    """MACD 黃金交叉：柱狀體翻正（最近 N 日內曾為負）且持續走強"""
    hist = macd_hist(df["Close"])
    was_negative = (hist <= 0).astype(float).shift(1).rolling(config.MACD_CROSS_DAYS).max() >= 1
    return (hist > 0) & was_negative & (hist >= hist.shift(1))


def rsi_strong(df: pd.DataFrame) -> pd.Series:
    """RSI 強勢：位於 50–75 多方區間且向上（強者恆強、不追過熱）"""
    r = rsi(df["Close"])
    return (r > config.RSI_LOW) & (r <= config.RSI_HIGH) & (r > r.shift(3))


def volume_price_surge(df: pd.DataFrame) -> pd.Series:
    """量價齊揚：近 3 日內出現攻擊日（2 倍量 + 漲 2% + 收當日高檔），
    且現價守住攻擊日收盤的 98%（無出貨跡象）"""
    c, v = df["Close"], df["Volume"]
    h, low = df["High"], df["Low"]
    avg_vol = v.rolling(20).mean()
    surge_day = (
        (v > avg_vol * config.VOL_SURGE_RATIO)
        & (c.pct_change() > config.VOL_SURGE_MIN_GAIN)
        & (c >= (h + low) / 2)
    )
    surge_recent = surge_day.astype(float).rolling(3).max() >= 1
    last_surge_close = c.where(surge_day).ffill()
    return surge_recent & (c >= last_surge_close * 0.98)


# 名稱 → 訊號函式（選股與回測共用）
ALL_SIGNALS = {
    "均線多頭": ma_alignment,
    "55日突破": breakout_55,
    "MACD金叉": macd_golden_cross,
    "RSI強勢": rsi_strong,
    "量價齊揚": volume_price_surge,
}
