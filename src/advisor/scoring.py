"""五維度評分引擎：趨勢 25 / 動能 30 / 量能 15 / 籌碼 20 / 營收 10

設計原則：
- 維度內的子項彼此互補、維度間盡量去相關，避免「同一件事計兩次分」
  （例：MACD 與 RSI 都是動能，被框在同一個 30 分上限內，不會疊加灌分）
- 連續量（RS 百分位、買超強度）給連續分數，優於布林一刀切
- 硬性排除條件一票否決，分數再高也不入選
- 缺資料的維度（法人、營收）不給 0 分懲罰，而是按可用權重把總分換算回 100
"""

import pandas as pd

from . import config
from . import signals
from .indicators import sma, atr


# ── 硬性排除 ────────────────────────────────────────────────

def hard_filter(df: pd.DataFrame) -> tuple[bool, str]:
    """一票否決條件，回傳 (是否通過, 未通過原因)"""
    c = df["Close"]
    if len(c) < config.MIN_BARS:
        return False, "歷史資料不足"

    close = float(c.iloc[-1])
    lookback = min(len(c), 252)
    high_52w = float(c.iloc[-lookback:].max())

    ma_long = sma(c, config.MA_LONG)
    if pd.notna(ma_long.iloc[-1]) and close < float(ma_long.iloc[-1]):
        return False, "跌破半年線（長期趨勢空方）"
    if close < high_52w * (1 - config.MAX_OFF_HIGH):
        return False, f"距 52 週高點超過 {config.MAX_OFF_HIGH:.0%}"
    if len(c) > 20 and close / float(c.iloc[-21]) - 1 > config.MAX_GAIN_20D:
        return False, f"20 日漲幅超過 {config.MAX_GAIN_20D:.0%}（過熱）"
    return True, ""


# ── 相對強度 ────────────────────────────────────────────────

def rs_raw(df: pd.DataFrame) -> float:
    """RS 原始分：加權近一季/半年/一月報酬（之後跨個股換算百分位）"""
    c = df["Close"]

    def ret(days: int) -> float:
        if len(c) <= days:
            return 0.0
        return float(c.iloc[-1] / c.iloc[-days - 1] - 1)

    return (config.RS_W_QTR * ret(config.RS_DAYS_QTR)
            + config.RS_W_HALF * ret(config.RS_DAYS_HALF)
            + config.RS_W_MONTH * ret(config.RS_DAYS_MONTH))


# ── 各維度評分 ──────────────────────────────────────────────

def trend_score(df: pd.DataFrame) -> float:
    """趨勢 30：多頭排列 12 + 季線上揚 6 + 距低點夠遠 6 + 貼近高點 6
    （Minervini 階段二趨勢模板的精神）"""
    c = df["Close"]
    close = float(c.iloc[-1])
    ma_m, ma_s, ma_l = sma(c, config.MA_MID), sma(c, config.MA_SLOW), sma(c, config.MA_LONG)
    lookback = min(len(c), 252)
    high_52w = float(c.iloc[-lookback:].max())
    low_52w = float(c.iloc[-lookback:].min())

    score = 0.0
    if (pd.notna(ma_l.iloc[-1])
            and close > ma_m.iloc[-1] > ma_s.iloc[-1] > ma_l.iloc[-1]):
        score += 12
    if pd.notna(ma_s.iloc[-10]) and ma_s.iloc[-1] > ma_s.iloc[-10]:
        score += 6
    if low_52w > 0 and close / low_52w - 1 >= 0.30:
        score += 6
    if close >= high_52w * 0.85:
        score += 6
    return score


def momentum_score(rs_pct: float, sig_today: dict[str, bool]) -> float:
    """動能 35：RS 百分位 0–20（連續）+ 突破 8 + MACD 4 + RSI 3
    （子項權重依 backtest.py 實測超額勝率排序：RS/突破 > MACD/RSI）"""
    score = rs_pct / 100 * 20
    if sig_today["55日突破"]:
        score += 8
    if sig_today["MACD金叉"]:
        score += 4
    if sig_today["RSI強勢"]:
        score += 3
    return score


def volume_score(df: pd.DataFrame, sig_today: dict[str, bool]) -> float:
    """量能 15：攻擊量 6 + 量能堆積 5 + 上漲日量 > 下跌日量 4"""
    v, c = df["Volume"], df["Close"]
    score = 0.0
    if sig_today["量價齊揚"]:
        score += 6
    vol20, vol60 = v.rolling(20).mean(), v.rolling(60).mean()
    if pd.notna(vol60.iloc[-1]) and vol20.iloc[-1] > vol60.iloc[-1]:
        score += 5  # 近月量能高於近季，籌碼正在累積
    chg = c.diff().iloc[-20:]
    v20 = v.iloc[-20:]
    up_vol = float(v20[chg > 0].mean()) if (chg > 0).any() else 0.0
    dn_vol = float(v20[chg < 0].mean()) if (chg < 0).any() else 0.0
    if up_vol > dn_vol:
        score += 4  # 量價配合：買盤日的量大於賣壓日
    return score


def chips_score(code: str, df: pd.DataFrame, inst: pd.DataFrame | None) -> float | None:
    """籌碼 10：外資連 3 日買超 6 + 買超強度 4。無資料回傳 None。

    chips_backtest 實測（2024-09 ~ 2026-06）：投信認養無超額（輸隨機買進），
    外資連買 +1.7pp 勝率、買超強度小幅正向——只保留有證據的兩項。"""
    if inst is None:
        return None
    if code not in inst.index:
        return 0.0
    row = inst.loc[code]
    foreign = row.filter(like="foreign_").fillna(0)
    trust = row.filter(like="trust_").fillna(0)
    combined = foreign.to_numpy() + trust.to_numpy()

    score = 0.0
    if len(foreign) and (foreign.to_numpy() > 0).all():
        score += 6  # 外資連續買超
    vol3 = float(df["Volume"].iloc[-config.INST_DAYS:].sum())
    if vol3 > 0 and combined.sum() / vol3 >= config.INST_STRENGTH_PCT:
        score += 4  # 買超占成交量比重夠大，不是零星敲單
    return score


def revenue_score(code: str, revenue: pd.DataFrame | None) -> float | None:
    """營收 10：最新月營收年增率分級（CANSLIM 的 C）。無資料回傳 None。"""
    if revenue is None or code not in revenue.index:
        return None
    yoy = revenue.loc[code, "yoy"]
    if pd.isna(yoy):
        return None
    if yoy >= config.REV_YOY_STRONG:
        return 10.0
    if yoy >= config.REV_YOY_GOOD:
        return 7.0
    if yoy > 0:
        return 4.0
    return 0.0


# ── 綜合評分 ───────────────────────────────────────────────

def score_stock(code: str, df: pd.DataFrame, rs_pct: float,
                inst: pd.DataFrame | None,
                revenue: pd.DataFrame | None) -> dict | None:
    """對單一個股完整評分；未過硬性排除回傳 None"""
    ok, _reason = hard_filter(df)
    if not ok:
        return None

    sig_today = {name: bool(fn(df).iloc[-1]) for name, fn in signals.ALL_SIGNALS.items()}

    is_etf = not (len(code) == 4 and code.isdigit())

    ch_score = None if is_etf else chips_score(code, df, inst)
    rev_score = None if is_etf else revenue_score(code, revenue)

    dims = [  # (得分, 權重)；得分為 None 表示該維度無資料
        (trend_score(df), config.W_TREND),
        (momentum_score(rs_pct, sig_today), config.W_MOMENTUM),
        (volume_score(df, sig_today), config.W_VOLUME),
        (ch_score, config.W_CHIPS),
        (rev_score, config.W_REVENUE),
    ]
    avail_w = sum(w for s, w in dims if s is not None)
    total = sum(s for s, _w in dims if s is not None) / avail_w * 100

    t, m, v, ch, rev = (s for s, _w in dims)
    close = float(df["Close"].iloc[-1])
    atr_val = float(atr(df).iloc[-1])
    
    def calc_strategy(mult):
        stop = close - mult * atr_val
        risk_per_share = max(close - stop, 1e-9)
        lots = int(config.CAPITAL * config.RISK_PER_TRADE / risk_per_share / 1000)
        return round(stop, 2), max(lots, 0)
        
    stop_short, lots_short = calc_strategy(config.ATR_STOP_MULT_SHORT)
    stop_swing, lots_swing = calc_strategy(config.ATR_STOP_MULT_SWING)
    stop_long, lots_long = calc_strategy(config.ATR_STOP_MULT_LONG)

    def _r(x):
        return round(x, 1) if x is not None else None

    return {
        "總分": round(total, 1),
        "趨勢": _r(t), "動能": _r(m), "量能": _r(v),
        "籌碼": _r(ch), "營收": _r(rev),
        "收盤價": round(close, 2),
        "短線停損": stop_short, "短線張數": lots_short,
        "波段停損": stop_swing, "波段張數": lots_swing,
        "長線停損": stop_long,  "長線張數": lots_long,
        "訊號": "、".join(n for n, on in sig_today.items() if on),
    }
