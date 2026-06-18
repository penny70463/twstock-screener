"""美股選股引擎：橫斷面動能策略（取代從台股移植的五因子）

回測結論（見 backtest_us.py 與專案記憶）：台股那套「週頻輪動＋緊停損」對打
美股的趨勢延續特性，全週期跑輸買進持有；補基本面因子也救不了。經 point-in-time
S&P 500（消除存活者偏誤）的參數搜尋，美股最佳「主動」配置為：

    波動率調整的 12-1 月橫斷面動能 + 個股絕對動能濾網（>0 且站上 200 日線）

本模組即此策略的「即時版」（只取最後一根 K 線排名），輸出欄位與
advisor.screener.run_screen 完全一致，前端無需改動。

總分 = 純波動率調整動能的橫斷面百分位（0–100），即回測驗證的排名依據。
趨勢/量能正規化為 0–100 僅供前端顯示參考，不進總分——backtest_us.py
（--strategy native vs deployed）實測：把趨勢/量能納入排名會增周轉、降夏普
（5y 1.41→1.14、8y 0.82→0.61），故上線採純動能排名。
籌碼/營收 美股無 → None（前端欄位留空）。
"""

import numpy as np
import pandas as pd

from . import config
from .indicators import sma, atr

FORMATION = 252      # 形成期（12 月）
SKIP = 21            # 跳過最近 1 月（避開短期反轉）
MA_TREND = 200       # 絕對動能濾網用的長期均線

OUT_COLS = ["代號", "名稱", "市場", "產業", "收盤價", "總分", "趨勢", "動能",
            "量能", "籌碼", "營收", "RS", "短線停損", "短線張數", "波段停損",
            "波段張數", "長線停損", "長線張數", "訊號", "sparkline"]


def momentum_raw(c: pd.Series) -> float | None:
    """波動率調整的 12-1 月動能：形成期報酬 / 形成期日報酬波動。None=資料不足。"""
    if len(c) < FORMATION + 1:
        return None
    ret = float(c.iloc[-1 - SKIP] / c.iloc[-FORMATION - 1] - 1)
    vol = float(c.pct_change().iloc[-FORMATION:].std())
    if not vol or np.isnan(vol):
        return None
    return ret / vol


def _trend_score(c: pd.Series) -> tuple[float, bool, list[str]]:
    """趨勢 25：站上 200 日線 10 + 200 日線上揚 8 + 貼近 52 週高 7。
    回傳 (分數, 是否站上200日線, 訊號文字)。"""
    close = float(c.iloc[-1])
    ma = sma(c, MA_TREND)
    above = pd.notna(ma.iloc[-1]) and close > float(ma.iloc[-1])
    rising = pd.notna(ma.iloc[-21]) and float(ma.iloc[-1]) > float(ma.iloc[-21])
    lookback = min(len(c), 252)
    high_52w = float(c.iloc[-lookback:].max())
    near_high = close >= high_52w * 0.85

    score = (above * 10.0) + (rising * 8.0) + (near_high * 7.0)
    sigs = []
    if above:
        sigs.append("站上200日線")
    if near_high:
        sigs.append("逼近52週高")
    return score, bool(above), sigs


def _volume_score(df: pd.DataFrame) -> tuple[float, list[str]]:
    """量能 15：近月量 > 近季量 8 + 漲日量 > 跌日量 7。"""
    v, c = df["Volume"], df["Close"]
    vol20, vol60 = v.rolling(20).mean(), v.rolling(60).mean()
    accumulating = pd.notna(vol60.iloc[-1]) and vol20.iloc[-1] > vol60.iloc[-1]
    chg = c.diff().iloc[-20:]
    v20 = v.iloc[-20:]
    up_vol = float(v20[chg > 0].mean()) if (chg > 0).any() else 0.0
    dn_vol = float(v20[chg < 0].mean()) if (chg < 0).any() else 0.0
    healthy = up_vol > dn_vol
    score = (accumulating * 8.0) + (healthy * 7.0)
    sigs = ["量能堆積"] if accumulating else []
    return score, sigs


def _stops(df: pd.DataFrame) -> dict:
    """ATR 停損與建議股數（沿用台股風控公式，美股以「股數」表示）。"""
    close = float(df["Close"].iloc[-1])
    a = float(atr(df).iloc[-1])

    def calc(mult):
        stop = close - mult * a
        risk = max(close - stop, 1e-9)
        shares = int(config.CAPITAL * config.RISK_PER_TRADE / risk)
        return round(stop, 2), max(shares, 0)

    s_stop, s_n = calc(config.ATR_STOP_MULT_SHORT)
    w_stop, w_n = calc(config.ATR_STOP_MULT_SWING)
    l_stop, l_n = calc(config.ATR_STOP_MULT_LONG)
    return {"短線停損": s_stop, "短線張數": s_n,
            "波段停損": w_stop, "波段張數": w_n,
            "長線停損": l_stop, "長線張數": l_n}


def run_screen(universe: pd.DataFrame,
               history: dict[str, pd.DataFrame],
               inst=None, revenue=None,
               threshold: float = 70.0,
               market: str = "US") -> tuple[pd.DataFrame, pd.DataFrame]:
    """美股動能選股。簽名與 advisor.screener.run_screen 對齊（inst/revenue 忽略）。

    回傳 (過濾後的股票, 全股票池評分)。ETF 計入評分供前端檢視，但不列入 screened。
    """
    meta = universe.set_index("code")
    etf_codes = set(meta.index[meta.get("industry") == "ETF"]) if "industry" in meta.columns else set()

    # RS 百分位跨「個股」計算（排除 ETF，避免大盤 ETF 稀釋相對強度）
    raw = {}
    for code, df in history.items():
        if code in etf_codes:
            continue
        m = momentum_raw(df["Close"])
        if m is not None:
            raw[code] = m
    rs_series = pd.Series(raw)
    rs_pct = rs_series.rank(pct=True) * 100 if len(rs_series) else pd.Series(dtype=float)

    universe_rows, rows = [], []
    for code, df in history.items():
        if df.empty or len(df) < config.MA_SLOW:
            continue
        c = df["Close"]
        close = float(c.iloc[-1])
        is_etf = code in etf_codes

        t_score, above_200, t_sigs = _trend_score(c)
        v_score, v_sigs = _volume_score(df)
        rs_val = float(rs_pct[code]) if code in rs_pct.index else 0.0
        mom = raw.get(code)

        # 總分 = 純波動率調整動能百分位（回測驗證的排名依據）。
        # 趨勢/量能正規化為 0–100 僅供顯示，不進總分——回測證實納入會增周轉、降績效。
        total = round(rs_val, 1)
        trend_disp = round(t_score / 25 * 100, 1)
        vol_disp = round(v_score / 15 * 100, 1)

        # 絕對動能濾網（個股層級的真防禦）：動能>0 且站上 200 日線
        passes = (not is_etf) and (mom is not None and mom > 0) and above_200

        sigs = []
        if mom is not None and mom > 0:
            sigs.append("動能多方")
        sigs += t_sigs + v_sigs
        if not passes and not is_etf:
            reason = "ETF" if is_etf else ("動能轉弱" if (mom is None or mom <= 0) else "未站上200日線")
            signal = f"未通過: {reason}"
        else:
            signal = "、".join(sigs)

        industry = str(meta.at[code, "industry"]) if code in meta.index and "industry" in meta.columns else ""
        row = {
            "代號": code,
            "名稱": meta.at[code, "name"] if code in meta.index else code,
            "市場": "US",
            "產業": industry,
            "收盤價": round(close, 2),
            "總分": total,
            "趨勢": trend_disp,
            "動能": total,        # 動能即總分（純動能策略）
            "量能": vol_disp,
            "籌碼": None,
            "營收": None,
            "RS": round(rs_val, 0),
            **_stops(df),
            "訊號": signal,
            "sparkline": [round(float(x), 2) for x in c.tail(20).tolist()],
        }
        universe_rows.append(row)
        if passes and total >= threshold:
            rows.append(row)

    if not universe_rows:
        return pd.DataFrame(), pd.DataFrame()

    universe_out = pd.DataFrame(universe_rows)[OUT_COLS].reset_index(drop=True)
    out = (pd.DataFrame(rows)[OUT_COLS].sort_values("總分", ascending=False).reset_index(drop=True)
           if rows else pd.DataFrame())
    return out, universe_out
