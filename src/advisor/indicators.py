"""共用技術指標（全部向量化，同時供即時選股與回測使用）"""

import pandas as pd

from . import config


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def rsi(close: pd.Series, period: int = config.RSI_PERIOD) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss
    return 100 - 100 / (1 + rs)


def macd_hist(close: pd.Series) -> pd.Series:
    fast = close.ewm(span=config.MACD_FAST, adjust=False).mean()
    slow = close.ewm(span=config.MACD_SLOW, adjust=False).mean()
    dif = fast - slow
    signal = dif.ewm(span=config.MACD_SIGNAL, adjust=False).mean()
    return dif - signal


def atr(df: pd.DataFrame, period: int = config.ATR_PERIOD) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()
