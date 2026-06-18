#!/usr/bin/env python3
"""美股組合層回測：用與台股同一套選股規則，實測美股的績效特性

移植自 stock_selector/portfolio_backtest.py，忠實沿用同一套評分／調倉／曝險
引擎，只改動「市場相依」的三處，以隔離出「同一策略換到美股」的純效果：

  1. 大盤濾網與曝險基準：^TWII → ^GSPC（S&P 500）
  2. 股票池：台股 600 檔 → 美股權值股（src/advisor/data.us_listings()），
     本檔預設「排除所有 ETF」，純測個股選股 alpha（ETF 另案回測）
  3. 交易成本：台股手續費 0.1425%×2＋證交稅 0.3% → 美股零佣金、無證交稅，
     僅以單邊 0.05% 估滑價（US_FEE）

策略本質提醒（與台股的差異）：
  美股個股代號非 4 碼數字，scoring 的 is_etf 永遠為真 → 籌碼(法人)與營收
  兩維永遠 None，總分只由「趨勢30＋動能35＋量能15」按可用權重換算回 100。
  等於台股五因子砍掉籌碼與營收，剩純技術動能。本回測如實反映此行為
  （chips 面板對美股回傳 None，退回技術 80 分制）。

誠實申報的限制：
  - universe 是「今日」的權值股清單，往回測有存活者偏誤 → 絕對報酬偏樂觀，
    請看「相對基準（SPY/QQQ）的結構差異」（回撤、夏普、獲利因子），別看絕對數字
  - 進出場以訊號當日收盤價成交（收盤後算分的簡化）
  - 美股已還原權息（auto_adjust=True），報酬近似含息；基準亦同口徑

用法：
    python3 backtest_us.py
    python3 backtest_us.py --period 5y --top 10
    python3 backtest_us.py --compare          # 比較各曝險變體
    python3 backtest_us.py --include-etf       # 把 ETF 也納入股票池
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from src.advisor import config
from src.advisor import data as adv_data

INDEX_SYMBOL = "^GSPC"          # 美股大盤濾網／曝險基準
BENCHMARKS = ["SPY", "QQQ"]    # 報表對照基準（含息，auto_adjust）
US_FEE = 0.0005                 # 美股單邊成本（零佣金，僅估滑價 5bp）
US_TAX = 0.0                    # 美股無證交稅
BROAD_ETF = {"SPY", "VOO", "QQQ", "DIA", "IWM"}  # 廣基大盤 ETF
OUTPUT_DIR = Path(__file__).parent / "data" / "results"


# ── 股票池 ──────────────────────────────────────────────────

def get_us_universe(limit: int, include_etf: bool) -> pd.DataFrame:
    df = adv_data.us_listings()
    if not include_etf:
        df = df[df["industry"] != "ETF"]
    return df.head(limit).reset_index(drop=True)


# ── 面板建構與向量化評分（與 scoring.py 的技術三維一致）──────────

def build_panels(history: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    fields = ("Close", "High", "Low", "Volume")
    return {
        f.lower(): pd.DataFrame({code: df[f] for code, df in history.items()})
        for f in fields
    }


EARN_WEIGHT = config.W_CHIPS + config.W_REVENUE  # 盈餘維度承接死掉的籌碼+營收權重(=20)


def panel_scores(p: dict[str, pd.DataFrame],
                 earnings: pd.DataFrame | None = None
                 ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """回傳 (每日評分面板, ATR 面板)。NaN 表示當日未過硬性排除。

    earnings=None（v1）：總分 = (趨勢30＋動能35＋量能15)/80×100，與 scoring
      對 is_etf 的處理一致（純技術動能）。
    earnings 面板（v2）：盈餘維度（0–20，盈餘驚奇+EPS年增）承接籌碼+營收的
      權重 → 有盈餘資料的日期 (tech+earn)/100×100，無資料的格退回技術 80 分制
      （與 portfolio_backtest 的籌碼面板 NaN 退回邏輯一致）。
    """
    close, high, low, vol = p["close"], p["high"], p["low"], p["volume"]

    ma20 = close.rolling(config.MA_MID).mean()
    ma60 = close.rolling(config.MA_SLOW).mean()
    ma120 = close.rolling(config.MA_LONG).mean()
    high252 = close.rolling(252, min_periods=config.MIN_BARS).max()
    low252 = close.rolling(252, min_periods=config.MIN_BARS).min()

    # 趨勢 30
    trend = (
        ((close > ma20) & (ma20 > ma60) & (ma60 > ma120)) * 12.0
        + (ma60 > ma60.shift(10)) * 6.0
        + (close / low252 - 1 >= 0.30) * 6.0
        + (close >= high252 * 0.85) * 6.0
    )

    # 動能 35：RS 百分位 20 + 突破 8 + MACD 4 + RSI 3
    rs_raw = (config.RS_W_QTR * (close / close.shift(config.RS_DAYS_QTR) - 1)
              + config.RS_W_HALF * (close / close.shift(config.RS_DAYS_HALF) - 1)
              + config.RS_W_MONTH * (close / close.shift(config.RS_DAYS_MONTH) - 1))
    rs_pct = rs_raw.rank(axis=1, pct=True) * 100

    prior_high = close.shift(1).rolling(config.BREAKOUT_WINDOW).max()
    breakout = (close > prior_high) & (vol > vol.rolling(20).mean() * config.BREAKOUT_VOL_RATIO)

    ema_f = close.ewm(span=config.MACD_FAST, adjust=False).mean()
    ema_s = close.ewm(span=config.MACD_SLOW, adjust=False).mean()
    dif = ema_f - ema_s
    hist = dif - dif.ewm(span=config.MACD_SIGNAL, adjust=False).mean()
    macd_x = ((hist > 0)
              & ((hist <= 0).astype(float).shift(1).rolling(config.MACD_CROSS_DAYS).max() >= 1)
              & (hist >= hist.shift(1)))

    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / config.RSI_PERIOD, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / config.RSI_PERIOD, adjust=False).mean()
    rsi = 100 - 100 / (1 + gain / loss)
    rsi_s = (rsi > config.RSI_LOW) & (rsi <= config.RSI_HIGH) & (rsi > rsi.shift(3))

    momentum = rs_pct / 100 * 20 + breakout * 8.0 + macd_x * 4.0 + rsi_s * 3.0

    # 量能 15：攻擊量 6 + 量能堆積 5 + 漲日量 > 跌日量 4
    avg_vol = vol.rolling(20).mean()
    surge_day = ((vol > avg_vol * config.VOL_SURGE_RATIO)
                 & (close.pct_change() > config.VOL_SURGE_MIN_GAIN)
                 & (close >= (high + low) / 2))
    vp = ((surge_day.astype(float).rolling(3).max() >= 1)
          & (close >= close.where(surge_day).ffill() * 0.98))
    up_vol = vol.where(delta > 0).rolling(20, min_periods=1).mean()
    dn_vol = vol.where(delta < 0).rolling(20, min_periods=1).mean()
    volume_dim = (vp * 6.0
                  + (vol.rolling(20).mean() > vol.rolling(60).mean()) * 5.0
                  + (up_vol.fillna(0) > dn_vol.fillna(0)) * 4.0)

    tech_w = config.W_TREND + config.W_MOMENTUM + config.W_VOLUME
    tech_pts = trend + momentum + volume_dim
    if earnings is None:
        total = tech_pts / tech_w * 100  # v1：純技術動能（美股無籌碼/營收）
    else:
        earn = earnings.reindex(index=close.index, columns=close.columns)
        full = (tech_pts + earn) / (tech_w + EARN_WEIGHT) * 100
        total = full.where(earn.notna(), tech_pts / tech_w * 100)  # 無盈餘資料退回技術分制

    # 硬性排除（與 scoring.hard_filter 一致）
    hard = ((close > ma120)
            & (close >= high252 * (1 - config.MAX_OFF_HIGH))
            & (close / close.shift(21) - 1 <= config.MAX_GAIN_20D)
            & ma120.notna() & close.notna())
    score = total.where(hard)

    prev_c = close.shift(1)
    tr = np.maximum(high - low, np.maximum((high - prev_c).abs(), (low - prev_c).abs()))
    atr = tr.ewm(alpha=1 / config.ATR_PERIOD, adjust=False).mean()
    return score, atr


# ── 美股原生動能策略（12-1 相對動能 + 雙動能趨勢濾網）──────────

def native_momentum_scores(p: dict[str, pd.DataFrame],
                           formation=(252,), skip: int = 21,
                           vol_adjust: bool = False, abs_filter: bool = True
                           ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """橫斷面動能（Jegadeesh-Titman 系），可參數化以供策略搜尋：

    - formation：形成期(交易日) tuple，多個則取各期百分位平均（多週期混合，更穩健）
    - skip：跳過最近 N 日（避開短期反轉，經典 1 月=21）
    - vol_adjust：以形成期波動率標準化報酬（風險調整後動能，常提升夏普）
    - abs_filter：絕對動能濾網（形成期報酬>0 且收盤>200日線），Antonacci 雙動能的個股腿
    """
    close = p["close"]
    ma200 = close.rolling(200, min_periods=config.MIN_BARS).mean()
    daily_ret = close.pct_change()

    rank_sum = None
    base_formation = None  # 用最長形成期判斷絕對動能方向
    for lb in formation:
        ret = close.shift(skip) / close.shift(lb) - 1
        if vol_adjust:
            vol = daily_ret.rolling(lb).std()
            ret = ret / vol.replace(0, np.nan)
        rank = ret.rank(axis=1, pct=True) * 100
        rank_sum = rank if rank_sum is None else rank_sum + rank
        if base_formation is None or lb == max(formation):
            base_formation = close.shift(skip) / close.shift(lb) - 1
    score = rank_sum / len(formation)

    if abs_filter:
        eligible = (base_formation > 0) & (close > ma200) & ma200.notna()
        score = score.where(eligible)

    high, low = p["high"], p["low"]
    prev_c = close.shift(1)
    tr = np.maximum(high - low, np.maximum((high - prev_c).abs(), (low - prev_c).abs()))
    atr = tr.ewm(alpha=1 / config.ATR_PERIOD, adjust=False).mean()
    return score, atr


def deployed_score_panel(p: dict[str, pd.DataFrame]
                         ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """「混合評分（動能60＋趨勢25＋量能15）」變體——已被否決，保留供重現對照。

    #3 保真度驗證的結果：此混合排名比純動能（--strategy native）差
    （5y 夏普 1.41→1.14、8y 0.82→0.61，且周轉約 2.5 倍），因趨勢/量能子項
    反覆翻動造成過度換股。故 src/advisor/us_screener 上線版已改為純動能排名，
    本函式僅用於 `--strategy deployed` 重現「為何不混合」的對照。
    """
    close, high, low, vol = p["close"], p["high"], p["low"], p["volume"]
    ma200 = close.rolling(200).mean()

    # 動能 60：波動率調整 12-1 動能的橫斷面百分位
    ret = close.shift(21) / close.shift(252) - 1
    mvol = close.pct_change().rolling(252).std()
    mom = ret / mvol.replace(0, np.nan)
    rs_pct = mom.rank(axis=1, pct=True) * 100
    mom_dim = rs_pct / 100 * 60.0

    # 趨勢 25：站上200日線10 + 200日線上揚8 + 距52週高15%內7
    high252 = close.rolling(252, min_periods=config.MIN_BARS).max()
    above = close > ma200
    trend_dim = (above * 10.0
                 + (ma200 > ma200.shift(21)) * 8.0
                 + (close >= high252 * 0.85) * 7.0)

    # 量能 15：近月量>近季量8 + 漲日量>跌日量7
    chg = close.diff()
    up_vol = vol.where(chg > 0).rolling(20, min_periods=1).mean()
    dn_vol = vol.where(chg < 0).rolling(20, min_periods=1).mean()
    vol_dim = ((vol.rolling(20).mean() > vol.rolling(60).mean()) * 8.0
               + (up_vol.fillna(0) > dn_vol.fillna(0)) * 7.0)

    total = mom_dim + trend_dim + vol_dim
    eligible = (mom > 0) & above & ma200.notna()
    score = total.where(eligible)

    prev_c = close.shift(1)
    tr = np.maximum(high - low, np.maximum((high - prev_c).abs(), (low - prev_c).abs()))
    atr = tr.ewm(alpha=1 / config.ATR_PERIOD, adjust=False).mean()
    return score, atr


def trend_filter_thresholds(index: pd.DatetimeIndex, period: str,
                            trend_filter: bool = True) -> pd.DataFrame:
    """雙動能的市場腿：S&P500 > 200 日線 → 滿倉(1.0)，否則空手(0.0)。
    trend_filter=False → 永遠滿倉。門檻設 0（純排名選股）。"""
    c = _index_close(period)
    ma200 = c.rolling(200).mean()
    expo = (c > ma200).astype(float) if trend_filter else pd.Series(1.0, index=c.index)
    out = pd.DataFrame({"thr": 0.0, "exposure": expo}, index=c.index)
    out = out.reindex(index).ffill()
    return out.fillna({"thr": 0.0, "exposure": 1.0 if not trend_filter else 0.0})


# ── 大盤門檻與曝險 ──────────────────────────────────────────

def _index_close(period: str) -> pd.Series:
    idx = yf.download(INDEX_SYMBOL, period=period, auto_adjust=True, progress=False)
    if isinstance(idx.columns, pd.MultiIndex):
        idx.columns = idx.columns.get_level_values(0)
    return idx["Close"].dropna()


def regime_thresholds(index: pd.DatetimeIndex, period: str,
                      exposure_map: dict[str, float]) -> pd.DataFrame:
    """每日入選門檻與目標曝險（依 S&P 500 多空狀態，與 market.get_regime 一致）"""
    c = _index_close(period)
    ma60, ma120 = c.rolling(60).mean(), c.rolling(120).mean()
    bull = (c > ma60) & (ma60 > ma120) & (ma60 > ma60.shift(10))
    bear = (c < ma120) & (c < ma60)
    out = pd.DataFrame({
        "thr": np.select([bull, bear], [config.SCORE_BULL, config.SCORE_BEAR],
                         default=config.SCORE_NEUTRAL),
        "exposure": np.select(
            [bull, bear],
            [exposure_map["多頭"], exposure_map["空頭"]],
            default=exposure_map["中性"]),
    }, index=c.index, dtype=float)
    out = out.reindex(index).ffill()
    return out.fillna({"thr": config.SCORE_NEUTRAL,
                       "exposure": exposure_map["中性"]})


def breadth_series(close_panel: pd.DataFrame) -> pd.Series:
    ma60 = close_panel.rolling(60).mean()
    above = (close_panel > ma60).sum(axis=1)
    valid = close_panel.notna().sum(axis=1).clip(lower=1)
    return above / valid


def exposure_series(index_close: pd.Series, breadth: pd.Series) -> pd.Series:
    """連續水位（與 market.exposure_series 同公式）：趨勢×0.5＋寬度×0.5，再波動率縮放"""
    ma60 = index_close.rolling(60).mean()
    ma120 = index_close.rolling(120).mean()
    trend = ((index_close > ma60) * 0.4
             + (ma60 > ma120) * 0.3
             + (ma60 > ma60.shift(10)) * 0.3)
    b = ((breadth.reindex(index_close.index).ffill() - config.BREADTH_LOW)
         / (config.BREADTH_HIGH - config.BREADTH_LOW)).clip(0, 1)
    base = config.EXP_W_TREND * trend + config.EXP_W_BREADTH * b
    realized = index_close.pct_change().rolling(20).std() * np.sqrt(252)
    vol_scale = (config.VOL_TARGET / realized).clip(upper=1.0)
    expo = (base * vol_scale).clip(0, 1)
    return (expo / config.EXP_STEP).round() * config.EXP_STEP


def build_thresholds(close_panel: pd.DataFrame, period: str,
                     variant: str | dict) -> pd.DataFrame:
    if variant == "cont" or variant is None:
        thr = regime_thresholds(close_panel.index, period, config.PF_EXPOSURE)
        expo = exposure_series(_index_close(period), breadth_series(close_panel))
        thr["exposure"] = expo.reindex(close_panel.index).ffill().fillna(0.5)
        return thr
    return regime_thresholds(close_panel.index, period, variant)


# ── 模擬引擎（與 portfolio_backtest.simulate 同邏輯，成本改美股）──────

def simulate(p: dict[str, pd.DataFrame], score: pd.DataFrame, atr: pd.DataFrame,
             thr: pd.DataFrame, top_n: int,
             stop_mult: float = config.ATR_STOP_MULT_SWING,
             rebalance_days: int = config.PF_REBALANCE_DAYS,
             use_stops: bool = True
             ) -> tuple[pd.Series, pd.DataFrame]:
    close = p["close"]
    close_ff = close.ffill()
    dates = close.index
    buffer_n = int(top_n * config.PF_HOLD_RANK_BUFFER)

    cash = float(config.CAPITAL)
    positions: dict[str, dict] = {}
    trades: list[dict] = []
    equity = []

    valid_counts = score.notna().sum(axis=1)
    start_candidates = np.flatnonzero(valid_counts.to_numpy() >= top_n)
    start = int(start_candidates[0]) if len(start_candidates) else len(dates)

    def sell(code: str, price: float, date, reason: str):
        nonlocal cash
        pos = positions.pop(code)
        proceeds = pos["shares"] * price * (1 - US_FEE - US_TAX)
        cash += proceeds
        trades.append({
            "code": code, "exit": date, "reason": reason,
            "ret": proceeds / pos["entry_cost"] - 1,
        })

    for t in range(len(dates)):
        date = dates[t]
        prices = close.iloc[t]

        # 1) 停損檢查（每日）
        if use_stops:
            for code in list(positions):
                px = prices.get(code)
                if pd.notna(px) and px < positions[code]["stop"]:
                    sell(code, float(px), date, "停損")

        # 2) 定頻調倉
        if t >= start and (t - start) % rebalance_days == 0:
            s = score.iloc[t].dropna().sort_values(ascending=False)
            eligible = s[s >= thr["thr"].iloc[t]]
            top_buffer = set(eligible.head(buffer_n).index)
            target_n = int(round(top_n * thr["exposure"].iloc[t]))

            for code in list(positions):
                if code not in top_buffer:
                    px = prices.get(code)
                    if pd.notna(px):
                        sell(code, float(px), date, "換股")

            if len(positions) > target_n:
                held_scores = {c: float(s.get(c, 0)) for c in positions}
                for code in sorted(held_scores, key=held_scores.get)[
                        :len(positions) - target_n]:
                    px = prices.get(code)
                    if pd.notna(px):
                        sell(code, float(px), date, "降曝險")

            equity_now = cash + sum(
                pos["shares"] * float(close_ff.iloc[t].get(c, 0) or 0)
                for c, pos in positions.items())
            slots = target_n - len(positions)
            for code in eligible.head(target_n).index:
                if slots <= 0:
                    break
                if code in positions:
                    continue
                px = prices.get(code)
                a = atr.iloc[t].get(code)
                if pd.isna(px) or pd.isna(a) or px <= 0:
                    continue
                budget = min(cash, equity_now / top_n)
                if budget < px:
                    continue
                shares = budget / (px * (1 + US_FEE))
                cost = shares * px * (1 + US_FEE)
                cash -= cost
                positions[code] = {
                    "shares": shares, "entry_cost": cost,
                    "stop": float(px) - stop_mult * float(a),
                }
                slots -= 1

        equity.append(cash + sum(
            pos["shares"] * float(close_ff.iloc[t].get(c, 0) or 0)
            for c, pos in positions.items()))

    return pd.Series(equity, index=dates, name="equity"), pd.DataFrame(trades)


# ── 績效與報表 ──────────────────────────────────────────────

def compute_metrics(equity: pd.Series, trades: pd.DataFrame) -> dict:
    eq = equity / equity.iloc[0]
    ret = eq.pct_change().dropna()
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    m = {
        "total": float(eq.iloc[-1] - 1),
        "cagr": float(eq.iloc[-1] ** (1 / years) - 1) if years > 0 else float("nan"),
        "mdd": float((eq / eq.cummax() - 1).min()),
        "sharpe": (float(ret.mean() / ret.std() * np.sqrt(252))
                   if ret.std() > 0 else float("nan")),
        "n_trades": len(trades),
        "win": float("nan"), "pf": float("nan"),
    }
    if not trades.empty:
        wins = trades["ret"] > 0
        m["win"] = float(wins.mean())
        loss_sum = trades.loc[~wins, "ret"].sum()
        m["pf"] = (float(trades.loc[wins, "ret"].sum() / -loss_sum)
                   if loss_sum < 0 else float("inf"))
    return m


def sub_sharpe(equity: pd.Series) -> float:
    eq = equity / equity.iloc[0]
    ret = eq.pct_change().dropna()
    return float(ret.mean() / ret.std() * np.sqrt(252)) if ret.std() > 0 else float("nan")


def benchmark(eq_index: pd.DatetimeIndex, symbol: str) -> dict:
    bm = yf.download(symbol, auto_adjust=True, progress=False,
                     start=eq_index[0], end=eq_index[-1] + pd.Timedelta(days=1))
    if isinstance(bm.columns, pd.MultiIndex):
        bm.columns = bm.columns.get_level_values(0)
    b = bm["Close"].dropna()
    be = b / b.iloc[0]
    ret = be.pct_change().dropna()
    years = (be.index[-1] - be.index[0]).days / 365.25
    return {"total": float(be.iloc[-1] - 1),
            "cagr": float(be.iloc[-1] ** (1 / years) - 1) if years > 0 else float("nan"),
            "mdd": float((be / be.cummax() - 1).min()),
            "sharpe": (float(ret.mean() / ret.std() * np.sqrt(252))
                       if ret.std() > 0 else float("nan"))}


def report(equity: pd.Series, trades: pd.DataFrame, biased: bool = True):
    m = compute_metrics(equity, trades)
    print(f"\n═══ 美股組合層回測（{equity.index[0]:%Y-%m-%d} ~ {equity.index[-1]:%Y-%m-%d}）═══")
    print(f"  策略總報酬   {m['total']:+8.1%}")
    print(f"  年化報酬     {m['cagr']:+8.1%}")
    print(f"  最大回撤     {m['mdd']:8.1%}")
    print(f"  夏普比率     {m['sharpe']:8.2f}")
    if not trades.empty:
        print(f"  交易次數     {m['n_trades']:5d}      勝率 {m['win']:.1%}")
        print(f"  獲利因子     {m['pf']:8.2f}")
        print("\n  出場原因統計：")
        for reason, row in trades.groupby("reason")["ret"].agg(["count", "mean"]).iterrows():
            print(f"    {reason}：{int(row['count'])} 筆，平均 {row['mean']:+.1%}")

    print("\n  ── 對照基準（同期、含息）──")
    for sym in BENCHMARKS:
        b = benchmark(equity.index, sym)
        print(f"    {sym:<4} 總報酬 {b['total']:+8.1%}   年化 {b['cagr']:+7.1%}   "
              f"回撤 {b['mdd']:7.1%}   夏普 {b['sharpe']:5.2f}")

    print("\n  已計入：單邊滑價 5bp、還原權息（報酬含息）。未計入：實際成交滑價、借券。")
    if biased:
        print("  ⚠ 股票池為今日權值股，存在存活者偏誤，絕對報酬偏樂觀——"
              "重點看相對基準的結構差異（回撤、夏普），而非絕對數字。")
    else:
        print("  ✓ 已用 point-in-time S&P 500 成分股，存活者偏誤大幅消除"
              "（殘留：~6% 下市股無價格資料）——絕對報酬可信度高。")


EXPOSURE_VARIANTS = {
    "off": {"多頭": 1.0, "中性": 1.0, "空頭": 1.0},
    "bear0": {"多頭": 1.0, "中性": 1.0, "空頭": 0.0},
    "half": {"多頭": 1.0, "中性": 0.5, "空頭": 0.0},
    "cont": None,
}


def run_search(panels, membership, args):
    """策略參數搜尋：在乾淨池上掃描動能配置，跨前後半期評估防過擬合。

    評斷：以全期夏普排序，但要求樣本內(前半)與樣本外(後半)夏普都不崩
    （後半夏普 >= 0）。最後對勝出配置做基準對照。
    """
    close = panels["close"]
    mid = close.index[len(close.index) // 2]
    print(f"  樣本內 {close.index[0]:%Y-%m} ~ {mid:%Y-%m}｜"
          f"樣本外 {mid:%Y-%m} ~ {close.index[-1]:%Y-%m}\n")

    # 搜尋空間（刻意精簡，降低多重比較的過擬合風險）
    FORMATIONS = {"6-1": (126,), "12-1": (252,), "mix3/6/12": (63, 126, 252)}
    grid = []
    for fname, f in FORMATIONS.items():
        for voladj in (False, True):
            for top_n in (10, 15, 20, 30):
                for reb in (10, 21):
                    for tf in (True, False):
                        grid.append((fname, f, voladj, top_n, reb, tf))

    rows = []
    for fname, f, voladj, top_n, reb, tf in grid:
        score, atr = native_momentum_scores(panels, formation=f, vol_adjust=voladj)
        if membership is not None:
            score = score.where(membership)
        thr = trend_filter_thresholds(close.index, args.period, trend_filter=tf)
        equity, trades = simulate(panels, score, atr, thr, top_n,
                                  rebalance_days=reb, use_stops=False)
        m = compute_metrics(equity, trades)
        rows.append({
            "形成期": fname, "波調": "Y" if voladj else "N", "檔數": top_n,
            "調倉日": reb, "趨勢濾網": "Y" if tf else "N",
            "夏普_全": round(m["sharpe"], 2),
            "夏普_內": round(sub_sharpe(equity.loc[:mid]), 2),
            "夏普_外": round(sub_sharpe(equity.loc[mid:]), 2),
            "年化%": round(m["cagr"] * 100, 1),
            "回撤%": round(m["mdd"] * 100, 1),
            "交易": m["n_trades"],
        })

    df = pd.DataFrame(rows)
    # 防過擬合：要求樣本內外夏普都 >= 0.3，再按全期夏普排序
    robust = df[(df["夏普_內"] >= 0.3) & (df["夏普_外"] >= 0.3)].copy()
    robust = robust.sort_values("夏普_全", ascending=False)
    with pd.option_context("display.unicode.east_asian_width", True):
        print(f"═══ 策略搜尋 Top 15（{args.period}，乾淨池，按全期夏普）═══")
        print("（已過濾：要求樣本內 & 樣本外夏普皆 >= 0.3，淘汰只在單一期間有效者）\n")
        print(robust.head(15).to_string(index=False))
    for sym in BENCHMARKS:
        b = benchmark(close.index, sym)
        print(f"\n  基準 {sym}：年化 {b['cagr']:+.1%}  夏普 {b['sharpe']:.2f}  回撤 {b['mdd']:.1%}")


def main():
    parser = argparse.ArgumentParser(description="美股組合層回測")
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--period", default="5y")
    parser.add_argument("--top", type=int, default=config.PF_TOP_N)
    parser.add_argument("--include-etf", action="store_true",
                        help="把 ETF 也納入股票池（預設排除）")
    parser.add_argument("--exposure", choices=list(EXPOSURE_VARIANTS), default=None)
    parser.add_argument("--compare", action="store_true", help="比較所有曝險變體")
    parser.add_argument("--factor", choices=["tech", "earnings"], default="tech",
                        help="tech=純技術(v1)；earnings=加盈餘驚奇+EPS年增維(v2)")
    parser.add_argument("--strategy", choices=["tw", "native", "deployed"], default="tw",
                        help="tw=台股移植；native=純波動率調整動能；"
                             "deployed=上線混合評分(動能60+趨勢25+量能15，複刻 us_screener)")
    parser.add_argument("--universe", choices=["megacap", "sp500"], default="megacap",
                        help="megacap=70檔權值股(有存活者偏誤)；"
                             "sp500=歷史成分股point-in-time(消除偏誤)")
    parser.add_argument("--search", action="store_true",
                        help="策略參數搜尋（強制 sp500 乾淨池，跨前後半期評估防過擬合）")
    parser.add_argument("--formation", choices=["6-1", "12-1", "mix"], default="12-1",
                        help="native 動能形成期")
    parser.add_argument("--vol-adjust", action="store_true",
                        help="native 用波動率調整動能（風險調整後）")
    parser.add_argument("--no-trend-filter", action="store_true",
                        help="native 關閉大盤 200 日線濾網（個股絕對動能已提供防禦）")
    parser.add_argument("--rebalance", type=int, default=21, help="native 調倉間隔(交易日)")
    args = parser.parse_args()

    FORMATION_MAP = {"6-1": (126,), "12-1": (252,), "mix": (63, 126, 252)}

    if args.search:
        args.universe = "sp500"  # 搜尋一律用乾淨池

    membership = None  # point-in-time 成分股遮罩（僅 sp500 universe）
    if args.universe == "sp500":
        from src.advisor import sp500_history
        years = int("".join(ch for ch in args.period if ch.isdigit()) or "5")
        print("【1/4】重建 S&P 500 歷史成分股（point-in-time）…")
        cal = pd.bdate_range(end=pd.Timestamp.today().normalize(),
                             start=pd.Timestamp.today().normalize()
                             - pd.DateOffset(years=years))
        memb_cal = sp500_history.membership_panel(cal)
        tickers = {t: t for t in memb_cal.columns}
        print(f"  期間出現過的成分股共 {len(tickers)} 檔（含已被剔除者）")
    else:
        print("【1/4】取得美股股票池…")
        universe = get_us_universe(args.limit, args.include_etf)
        tickers = dict(zip(universe["code"], universe["yahoo"]))
        print(f"  共 {len(universe)} 檔（{'含' if args.include_etf else '不含'} ETF）")

    print("【2/4】下載歷史股價…")
    history = adv_data.fetch_history(tickers, period=args.period)
    print(f"  成功取得 {len(history)} 檔")

    print("【3/4】向量化評分與大盤門檻…")
    panels = build_panels(history)
    if args.universe == "sp500":
        from src.advisor import sp500_history
        membership = sp500_history.membership_panel(panels["close"].index)
        membership = membership.reindex(columns=panels["close"].columns).fillna(False)
        cov = len(history) / len(tickers)
        print(f"  成分股價格涵蓋率 {cov:.0%}（抓不到的下市股已排除＝殘留偏誤）")

    if args.search:
        run_search(panels, membership, args)
        return

    # 動能類策略：橫斷面動能 + 雙動能濾網，定頻、無緊停損
    if args.strategy in ("native", "deployed"):
        if args.strategy == "deployed":
            score, atr = deployed_score_panel(panels)
            desc = "上線混合評分(動能60+趨勢25+量能15)"
        else:
            score, atr = native_momentum_scores(
                panels, formation=FORMATION_MAP[args.formation],
                vol_adjust=args.vol_adjust)
            desc = f"純動能({args.formation}{'＋波調' if args.vol_adjust else ''})"
        if membership is not None:
            score = score.where(membership)  # 每日只排名「當時的成分股」
        tf = not args.no_trend_filter
        thr = trend_filter_thresholds(panels["close"].index, args.period, trend_filter=tf)
        print(f"【4/4】{desc}（前 {args.top} 強等權、"
              f"每 {args.rebalance} 日調倉、大盤濾網{'開' if tf else '關'}、無緊停損）…")
        equity, trades = simulate(panels, score, atr, thr, args.top,
                                  rebalance_days=args.rebalance, use_stops=False)
        report(equity, trades, biased=(membership is None))
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        equity.to_csv(OUTPUT_DIR / "equity_curve_us.csv")
        print("\n  權益曲線已存至 data/results/equity_curve_us.csv")
        return

    earn_panel = None
    if args.factor == "earnings":
        from src.advisor import us_fundamentals
        print("  抓取歷史財報並建盈餘維度面板（point-in-time）…")
        earn_panel = us_fundamentals.earnings_score_panel(
            list(panels["close"].columns), panels["close"].index)
        cov = earn_panel.notna().any(axis=1).mean()
        print(f"  盈餘維度涵蓋 {cov:.0%} 的回測期間")
    score, atr = panel_scores(panels, earn_panel)
    if membership is not None:
        score = score.where(membership)  # 每日只排名「當時的成分股」

    if args.compare:
        print("【4/4】比較曝險變體…")
        rows = []
        for name, exp_map in EXPOSURE_VARIANTS.items():
            thr = build_thresholds(panels["close"], args.period,
                                   "cont" if name == "cont" else exp_map)
            equity, trades = simulate(panels, score, atr, thr, args.top)
            m = compute_metrics(equity, trades)
            rows.append({"變體": name,
                         "多/中/空": "連續模型" if name == "cont" else
                         f"{exp_map['多頭']:.0%}/{exp_map['中性']:.0%}/{exp_map['空頭']:.0%}",
                         "總報酬%": round(m["total"] * 100, 1),
                         "年化%": round(m["cagr"] * 100, 1),
                         "最大回撤%": round(m["mdd"] * 100, 1),
                         "夏普": round(m["sharpe"], 2),
                         "勝率%": round(m["win"] * 100, 1),
                         "獲利因子": round(m["pf"], 2),
                         "交易數": m["n_trades"]})
        for sym in BENCHMARKS:
            b = benchmark(panels["close"].index, sym)
            rows.append({"變體": sym, "多/中/空": "買進持有",
                         "總報酬%": round(b["total"] * 100, 1),
                         "年化%": round(b["cagr"] * 100, 1),
                         "最大回撤%": round(b["mdd"] * 100, 1),
                         "夏普": round(b["sharpe"], 2)})
        with pd.option_context("display.unicode.east_asian_width", True):
            print(f"\n═══ 美股曝險變體比較（{args.period}）═══\n")
            print(pd.DataFrame(rows).to_string(index=False))
        return

    exp_map = (EXPOSURE_VARIANTS[args.exposure] if args.exposure
               else config.PF_EXPOSURE)
    thr = build_thresholds(panels["close"], args.period,
                           "cont" if args.exposure == "cont" else exp_map)
    label = "連續模型" if args.exposure == "cont" else exp_map
    print(f"【4/4】模擬持倉（前 {args.top} 強等權、每 {config.PF_REBALANCE_DAYS} 日調倉、"
          f"{config.ATR_STOP_MULT_SWING:.1f}×ATR 停損、曝險 {label}）…")
    equity, trades = simulate(panels, score, atr, thr, args.top)
    report(equity, trades, biased=(membership is None))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    equity.to_csv(OUTPUT_DIR / "equity_curve_us.csv")
    print("\n  權益曲線已存至 data/results/equity_curve_us.csv")


if __name__ == "__main__":
    main()
