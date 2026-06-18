"""美股基本面因子（盈餘驚奇 + EPS 年增），point-in-time 乾淨可回測

台股策略的「籌碼」與「營收」兩維在美股永遠 None（見 scoring.is_etf），
本模組用美股拿得到、且有實證基礎的盈餘因子補回這塊：

  - 盈餘驚奇（Earnings Surprise / PEAD）：實際 EPS 超出分析師預估，
    公布後股價持續漂移（post-earnings-announcement drift，美股最穩健的
    異常之一，Bernard & Thomas 1989）
  - EPS 年增加速（CANSLIM 的 "C"）：本季 EPS vs 去年同季

關鍵設計：資料來源 yfinance 的 get_earnings_dates 每筆都帶「公布日期」，
因此能嚴格 point-in-time——每個交易日只採用「公布日 <= 當日」的財報，
回測無前視偏誤（lookahead bias）。
"""

import datetime as dt
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

CACHE_DIR = Path(__file__).parent / "cache"
DRIFT_WINDOW = 63          # PEAD 漂移觀察窗（約一季交易日）
EPS_YOY_STRONG = 0.25      # EPS 年增 >=25% 拿滿分（對應 CANSLIM C）
EPS_YOY_GOOD = 0.10


def _cache_path() -> Path:
    return CACHE_DIR / f"us_earnings_{dt.date.today():%Y%m%d}.pkl"


def fetch_earnings(tickers: list[str], limit: int = 24,
                   use_cache: bool = True) -> dict[str, pd.DataFrame]:
    """每檔的歷史財報事件表（index=公布日, 欄位 reported_eps / surprise_pct）。

    limit=24 約涵蓋 6 年季報，足供 5 年回測。當日已抓過直接讀快取。
    """
    CACHE_DIR.mkdir(exist_ok=True)
    path = _cache_path()
    cached: dict[str, pd.DataFrame] = {}
    if use_cache and path.exists():
        try:
            cached = pickle.loads(path.read_bytes())
        except Exception:
            cached = {}

    out: dict[str, pd.DataFrame] = {}
    missing = [t for t in tickers if t not in cached]
    for i, sym in enumerate(missing, 1):
        try:
            ed = yf.Ticker(sym).get_earnings_dates(limit=limit)
            df = ed[["Reported EPS", "Surprise(%)"]].dropna(how="all").copy()
            df.columns = ["reported_eps", "surprise_pct"]
            df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
            df = df[df["reported_eps"].notna()].sort_index()
            df = df[~df.index.duplicated(keep="last")]
            cached[sym] = df
        except Exception:
            cached[sym] = pd.DataFrame(columns=["reported_eps", "surprise_pct"])
        if i % 20 == 0:
            print(f"  已抓 {i}/{len(missing)} 檔財報")

    if missing:
        path.write_bytes(pickle.dumps(cached))
    out = {t: cached[t] for t in tickers if t in cached}
    return out


def _eps_yoy(events: pd.DataFrame) -> pd.Series:
    """逐筆財報的 EPS 年增率（本季 vs 4 季前），index 為公布日"""
    eps = events["reported_eps"]
    prev = eps.shift(4)
    # 用絕對值當分母避免去年虧損時符號翻轉；去年<=0 時年增不可靠 → NaN
    yoy = (eps - prev) / prev.abs()
    yoy[prev <= 0] = np.nan
    return yoy


def earnings_score_panel(tickers: list[str],
                         trading_index: pd.DatetimeIndex,
                         limit: int = 24) -> pd.DataFrame:
    """盈餘維度每日評分面板（0–20），嚴格 point-in-time。

    每個交易日 t，對每檔股票取「公布日 <= t」的最近一筆財報：
      EPS 年增分（CANSLIM C，0–12）：>=25%→12, >=10%→8, >0→4, else 0
      盈餘驚奇分（PEAD，0–8）：最近財報 surprise>0 且在漂移窗內→8,
                                surprise>0 但較舊→4, 落後預期→0
    無已公布財報的日期回傳 NaN（呼叫端退回純技術分制）。
    """
    earnings = fetch_earnings(tickers, limit=limit)
    cols = {}
    for sym in tickers:
        ev = earnings.get(sym)
        if ev is None or ev.empty:
            cols[sym] = pd.Series(np.nan, index=trading_index)
            continue
        ev = ev[~ev.index.duplicated(keep="last")].sort_index()
        yoy = _eps_yoy(ev)

        # 逐財報事件算分，再 asof 對映到交易日（只用已公布的）
        c_pts = pd.cut(yoy, [-np.inf, 0, EPS_YOY_GOOD, EPS_YOY_STRONG, np.inf],
                       labels=[0, 4, 8, 12]).astype(float).fillna(0.0)
        surprise_pos = (ev["surprise_pct"] > 0).astype(float)

        # point-in-time：每個交易日 ffill 最近一筆「公布日 <= 當日」的財報
        c_daily = c_pts.reindex(trading_index, method="ffill")
        sp_daily = surprise_pos.reindex(trading_index, method="ffill")
        last_report = pd.Series(ev.index, index=ev.index).reindex(
            trading_index, method="ffill")
        days_since = (trading_index - pd.DatetimeIndex(last_report)).days
        within_drift = days_since <= int(DRIFT_WINDOW * 1.6)  # 日曆日 ≈ 交易日窗

        surprise_pts = np.where(sp_daily == 1,
                                np.where(within_drift, 8.0, 4.0), 0.0)
        score = c_daily.fillna(0.0) + surprise_pts
        score[last_report.isna()] = np.nan  # 尚無已公布財報
        cols[sym] = score

    return pd.DataFrame(cols, index=trading_index)
