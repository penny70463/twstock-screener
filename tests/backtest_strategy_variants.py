"""台股策略變體回測：入選門檻來源（#2）與出場規則（#7）

背景（2026-10 策略檢討）：
    #2 入選門檻用 get_regime 的三態（70/75/80），曝險水位用連續三因子，
       兩套可能矛盾（門檻判多頭、水位卻只有四成）。測試門檻改跟水位走。
    #7 管線只產每日進場清單，沒有系統化出場規則。測試幾種出場規則的組合績效。

方法：
    - 資料：src/advisor/cache/hist_{period}_*.pkl（生產同源日線）+ universe_tw.json
    - 每 step 個交易日一個評分日，截斷歷史後呼叫生產版 screener.run_screen
      （inst/revenue 缺料，權重自動換算——與 backtest_tw_ranking 相同）
    - 評分結果快取到 data/backtests/，第二次執行秒級
    - 大盤指數用快取中的 0050 代替 ^TWII（離線、與 backtest_tw_ranking 一致）；
      寬度用快取股票池收盤面板，與生產 get_exposure_live 同一條 breadth_series

預先登記的採納門檻（跑之前寫死，避免看結果挑標準）：
    #2 門檻變體 T1/T2 要取代 T0，需同時滿足：
       全樣本 Top10 的 T+5 與 T+20「平均報酬」與「超額命中 P−U」四項都贏 T0，
       且中性狀態（樣本 ≥30 期）T+5 報酬不輸 T0。
    #7 出場變體要成為建議規則，需同時滿足：
       全期與最近 3 年兩段的夏普都高於 X0，且最大回撤不比 X0 差超過 3pp。

限制：
    - 股票池是「今日」universe 回溯，有存活者偏誤 → 只看變體間相對差異
    - 進出場以訊號日收盤成交（停損類出場為「觸發次日收盤」），略為樂觀，各變體一致
    - 無籌碼/營收維度（歷史資料不可得）

用法:
    python tests/backtest_strategy_variants.py --test threshold --period 5y
    python tests/backtest_strategy_variants.py --test exit --period 5y
    python tests/backtest_strategy_variants.py --test all --period 5y
"""
import argparse
import glob
import os
import pickle
import sys
sys.path.insert(0, ".")

import numpy as np
import pandas as pd

from src.advisor import config, screener
from src.advisor import market as adv_market
from src.advisor.indicators import atr, sma

sys.path.insert(0, "tests")
from backtest_tw_ranking import (  # noqa: E402  共用載入與狀態判定，避免兩套邏輯漂移
    load_history, load_universe, regime_on, forward_return, SCORE_BY_REGIME,
    WARMUP_BARS)

HORIZONS = (5, 10, 20)
TOP_N = 10


# ── 共用：逐期評分快取 ──────────────────────────────────────────

def _cache_path(period: str, step: int) -> str:
    src = sorted(glob.glob(f"src/advisor/cache/hist_{period}_*.pkl"))[-1]
    tag = os.path.basename(src).replace(".pkl", "")
    return f"data/backtests/screens_{tag}_step{step}.pkl"


def build_screens(hist: dict, universe: pd.DataFrame, step: int,
                  cache_file: str) -> dict:
    """{date: DataFrame[code, score, passed, chg]}，全股票池（門檻 0）。"""
    if os.path.exists(cache_file):
        print(f"[>] 載入評分快取: {cache_file}")
        return pickle.load(open(cache_file, "rb"))
    calendar = max((df.index for df in hist.values()), key=len)
    dates = [calendar[i] for i in range(WARMUP_BARS, len(calendar), step)]
    print(f"[>] 逐期評分 {len(dates)} 期（首次較久，之後讀快取）")
    out = {}
    for n, d in enumerate(dates, 1):
        trunc = {c: df.loc[:d] for c, df in hist.items()}
        trunc = {c: s for c, s in trunc.items()
                 if len(s) >= config.MIN_BARS and s.index[-1] == d}
        if len(trunc) < 100:
            continue
        _, uni = screener.run_screen(universe, trunc, None, None, threshold=0)
        if uni.empty:
            continue
        passed = ~uni["訊號"].astype(str).str.startswith("未通過")
        chg = {c: float(trunc[c]["Close"].iloc[-1] / trunc[c]["Close"].iloc[-2] - 1)
               for c in uni["代號"] if len(trunc[c]) >= 2}
        out[d] = pd.DataFrame({
            "code": uni["代號"].values,
            "score": uni["總分"].astype(float).values,
            "passed": passed.values,
            "chg": uni["代號"].map(chg).values,
        })
        if n % 20 == 0:
            print(f"  ... {n}/{len(dates)}")
    os.makedirs(os.path.dirname(cache_file), exist_ok=True)
    pickle.dump(out, open(cache_file, "wb"))
    print(f"[+] 已寫入評分快取 {cache_file}")
    return out


def picks_for(sc: pd.DataFrame, threshold: float, top_n: int = TOP_N) -> list[str]:
    """生產排序（變體 A）：過門檻 → 今日上漲 → 依漲幅取前 N。"""
    ok = sc[sc["passed"] & (sc["score"] >= threshold) & (sc["chg"] > 0)]
    return ok.sort_values("chg", ascending=False).head(top_n)["code"].tolist()


def exposure_series_offline(hist: dict, idx_close: pd.Series) -> pd.Series:
    panel = pd.DataFrame({c: df["Close"] for c, df in hist.items()})
    breadth = adv_market.breadth_series(panel)
    return adv_market.exposure_series(idx_close, breadth, market="TW")


# ── #2 門檻來源 ───────────────────────────────────────────────

def thr_T1(expo: float) -> float:
    """水位分段：≥70% → 70、≥40% → 75、其餘 80（對應三態門檻）"""
    if expo >= 0.70:
        return config.SCORE_BULL
    if expo >= 0.40:
        return config.SCORE_NEUTRAL
    return config.SCORE_BEAR


def thr_T2(expo: float) -> float:
    """水位線性：80 − 10 × 水位（水位 100% → 70、0% → 80）"""
    return config.SCORE_BEAR - (config.SCORE_BEAR - config.SCORE_BULL) * expo


def test_threshold(hist, screens, idx_close, expo, eval_years=None):
    dates = sorted(screens)
    max_h = max(HORIZONS)
    cal = idx_close.index
    dates = [d for d in dates if cal.get_loc(d) + max_h < len(cal)]
    if eval_years:
        cut = dates[-1] - pd.DateOffset(years=eval_years)
        dates = [d for d in dates if d >= cut]

    variants = ("T0", "T1", "T2")
    store = {v: {k: {h: [] for h in HORIZONS} for k in ("ret", "lift", "ret3", "lift3")}
             for v in variants}
    by_reg = {}
    agree = {}
    empty = {v: 0 for v in variants}
    n_eval = 0
    for d in dates:
        rg = regime_on(idx_close, d)
        if rg not in SCORE_BY_REGIME or d not in expo.index or pd.isna(expo.loc[d]):
            continue
        e = float(expo.loc[d])
        thr = {"T0": SCORE_BY_REGIME[rg], "T1": thr_T1(e), "T2": thr_T2(e)}
        bucket = "≥70%" if e >= 0.7 else ("40–70%" if e >= 0.4 else "<40%")
        agree[(rg, bucket)] = agree.get((rg, bucket), 0) + 1
        n_eval += 1
        sc = screens[d]
        univ = {h: [r for c in sc["code"] if c in hist
                    and (r := forward_return(hist[c], d, h)) is not None] for h in HORIZONS}
        for v in variants:
            codes = picks_for(sc, thr[v])
            if not codes:
                empty[v] += 1
                continue
            for h in HORIZONS:
                u_hit = float(np.mean([x > 0 for x in univ[h]])) if univ[h] else np.nan
                for key, cs in (("", codes), ("3", codes[:3])):
                    rets = [r for c in cs if (r := forward_return(hist[c], d, h)) is not None]
                    if not rets:
                        continue
                    m = float(np.mean(rets))
                    lift = float(np.mean([x > 0 for x in rets])) - u_hit
                    store[v]["ret" + key][h].append(m)
                    store[v]["lift" + key][h].append(lift)
                    if key == "":
                        by_reg.setdefault(rg, {w: {h2: [] for h2 in HORIZONS} for w in variants})
                        by_reg[rg][v][h].append(m)

    print(f"\n{'='*78}\n  #2 入選門檻來源（{n_eval} 期，{dates[0].date()} ~ {dates[-1].date()}）\n{'='*78}")
    print("  T0 = 三態門檻（現行）  T1 = 水位分段 70/75/80  T2 = 80 − 10×水位")
    print("\n  三態 × 水位分桶 交叉（期數）：")
    for rg in ("多頭", "中性", "空頭"):
        row = "  ".join(f"{b}:{agree.get((rg, b), 0):>3d}" for b in ("≥70%", "40–70%", "<40%"))
        print(f"    {rg}  {row}")
    print(f"\n  無標的期數：" + "、".join(f"{v} {empty[v]}" for v in variants))
    for key, label in (("", f"Top{TOP_N}"), ("3", "Top3")):
        print(f"\n  ── {label} ──")
        print(f"  {'':4s}" + "".join(f"   T+{h:<2d}報酬  P−U  " for h in HORIZONS))
        for v in variants:
            cells = []
            for h in HORIZONS:
                r = np.array(store[v]["ret" + key][h])
                lf = np.array(store[v]["lift" + key][h])
                cells.append(f"{r.mean()*100:>8.2f}% {lf.mean()*100:>+5.1f}pp" if len(r) else "      n/a        ")
            print(f"  {v:4s}" + "".join(cells))
    print("\n  ── 分狀態 Top10 平均報酬（T+5 / T+20）──")
    for rg, dct in by_reg.items():
        n = len(dct["T0"][5])
        cells = "  ".join(
            f"{v} {np.mean(dct[v][5])*100:5.2f}%/{np.mean(dct[v][20])*100:5.2f}%"
            if dct[v][5] else f"{v} n/a" for v in variants)
        print(f"    {rg}（{n} 期）  {cells}")


# ── #7 出場規則 ───────────────────────────────────────────────

EXIT_VARIANTS = {
    "X0 週輪動": dict(hold="rotate", stop=None),
    "X1 未跌出門檻續抱": dict(hold="qualified", stop=None),
    "X2 X1+ATR移動停損2.5": dict(hold="qualified", stop="atr"),
    "X3 X1+跌破MA20": dict(hold="qualified", stop="ma20"),
    "X4 X0+ATR移動停損2.5": dict(hold="rotate", stop="atr"),
}
# 穩健性（--robust）：X3/X2 的鄰近參數，確認不是單點最佳化
ROBUST_VARIANTS = {
    "X0 週輪動": dict(hold="rotate", stop=None),
    "X1 未跌出門檻續抱": dict(hold="qualified", stop=None),
    "X1+跌破MA10": dict(hold="qualified", stop="ma20", ma=10),
    "X1+跌破MA20": dict(hold="qualified", stop="ma20", ma=20),
    "X1+跌破MA40": dict(hold="qualified", stop="ma20", ma=40),
    "X1+跌破MA60": dict(hold="qualified", stop="ma20", ma=60),
    "X1+ATR移動1.5": dict(hold="qualified", stop="atr", k=1.5),
    "X1+ATR移動3.5": dict(hold="qualified", stop="atr", k=3.5),
    "X0+跌破MA20": dict(hold="rotate", stop="ma20", ma=20),
}


def simulate_exit(hist, screens, idx_close, variant: dict, start, end) -> dict:
    """10 槽等權、空槽補新進場；成本：買 0.1425%、賣 0.1425%+0.3%。"""
    rb_dates = [d for d in sorted(screens) if start <= d <= end]
    if not rb_dates:
        return {}
    cal = idx_close.loc[rb_dates[0]:end].index
    close = pd.DataFrame({c: df["Close"] for c, df in hist.items()}).reindex(cal).ffill()
    need_atr = variant["stop"] == "atr"
    need_ma = variant["stop"] == "ma20"
    atr_df = (pd.DataFrame({c: atr(df) for c, df in hist.items()}).reindex(cal).ffill()
              if need_atr else None)
    ma_n = variant.get("ma", 20)
    atr_k = variant.get("k", config.ATR_STOP_MULT_LONG)
    ma_df = (pd.DataFrame({c: sma(df["Close"], ma_n) for c, df in hist.items()}).reindex(cal).ffill()
             if need_ma else None)

    rb_set = set(rb_dates)
    cash = 1.0
    pos: dict[str, dict] = {}   # code -> {shares, entry_day, peak}
    pending_exit: set[str] = set()
    equity, turnover, holds, trades = [], 0.0, [], 0
    buy_cost, sell_cost = config.PF_FEE, config.PF_FEE + config.PF_TAX

    def value(d):
        return cash + sum(p["shares"] * close.at[d, c] for c, p in pos.items()
                          if pd.notna(close.at[d, c]))

    def sell(c, d, i):
        nonlocal cash, turnover, trades
        p = pos.pop(c)
        px = close.at[d, c]
        amt = p["shares"] * px
        cash += amt * (1 - sell_cost)
        turnover += amt
        holds.append(i - p["entry_i"])
        trades += 1

    for i, d in enumerate(cal):
        # 1) 前一日觸發的停損，今日收盤出場
        for c in list(pending_exit):
            if c in pos:
                sell(c, d, i)
        pending_exit.clear()

        # 2) 調倉日：依規則賣出，再補新進場
        if d in rb_set:
            sc = screens[d]
            rg = regime_on(idx_close, d)
            thr = SCORE_BY_REGIME.get(rg, config.SCORE_NEUTRAL)
            picks = picks_for(sc, thr)
            sc_idx = sc.set_index("code")
            for c in list(pos):
                if variant["hold"] == "rotate":
                    drop = c not in picks
                else:
                    ok = (c in sc_idx.index and bool(sc_idx.at[c, "passed"])
                          and sc_idx.at[c, "score"] >= thr - config.ADV_EXIT_MARGIN)
                    drop = not ok
                if drop:
                    sell(c, d, i)
            slots = TOP_N - len(pos)
            new = [c for c in picks if c not in pos and pd.notna(close.at[d, c])][:slots]
            if new:
                tgt = value(d) / TOP_N
                for c in new:
                    amt = min(tgt, cash / (1 + buy_cost))
                    if amt <= 0:
                        break
                    px = close.at[d, c]
                    pos[c] = {"shares": amt / px, "entry_i": i, "peak": px}
                    cash -= amt * (1 + buy_cost)
                    turnover += amt

        # 3) 停損檢查（以今日收盤判斷，次日收盤出場）
        for c, p in pos.items():
            px = close.at[d, c]
            if pd.isna(px):
                continue
            p["peak"] = max(p["peak"], px)
            if need_atr:
                a = atr_df.at[d, c]
                if pd.notna(a) and px < p["peak"] - atr_k * a:
                    pending_exit.add(c)
            elif need_ma:
                m = ma_df.at[d, c]
                if pd.notna(m) and px < m:
                    pending_exit.add(c)
        equity.append(value(d))

    eq = pd.Series(equity, index=cal)
    ret = eq.pct_change().dropna()
    yrs = len(ret) / 252
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    vol = ret.std() * np.sqrt(252)
    return {
        "cagr": cagr * 100, "vol": vol * 100,
        "sharpe": cagr / vol if vol > 0 else 0.0,
        "maxdd": float((eq / eq.cummax() - 1).min()) * 100,
        "turnover": turnover / float(eq.mean()) / yrs,
        "hold": float(np.mean(holds)) if holds else float("nan"),
        "trades": trades,
    }


def test_exit(hist, screens, idx_close, variants=None):
    dates = sorted(screens)
    end = dates[-1]
    segments = {"全期": dates[0], "近3年": end - pd.DateOffset(years=3)}
    print(f"\n{'='*78}\n  #7 出場規則（10 槽等權，含手續費+證交稅）\n{'='*78}")
    for seg, start in segments.items():
        sub = idx_close.loc[start:end]
        bh = (sub.iloc[-1] / sub.iloc[0]) ** (252 / len(sub)) - 1
        bh_vol = sub.pct_change().std() * np.sqrt(252)
        bh_dd = float((sub / sub.cummax() - 1).min()) * 100
        print(f"\n  ── {seg}（{pd.Timestamp(start).date()} ~ {end.date()}）──")
        print(f"  {'變體':<24s} {'年化':>7s} {'波動':>7s} {'夏普':>5s} {'回撤':>7s} {'年周轉':>6s} {'持有日':>6s} {'交易數':>6s}")
        print(f"  {'0050 買進持有':<24s} {bh*100:>6.2f}% {bh_vol*100:>6.2f}% {bh/bh_vol:>5.2f} {bh_dd:>6.2f}%")
        for name, v in (variants or EXIT_VARIANTS).items():
            m = simulate_exit(hist, screens, idx_close, v, start, end)
            print(f"  {name:<24s} {m['cagr']:>6.2f}% {m['vol']:>6.2f}% {m['sharpe']:>5.2f} "
                  f"{m['maxdd']:>6.2f}% {m['turnover']:>5.1f}x {m['hold']:>6.1f} {m['trades']:>6d}")


def main():
    ap = argparse.ArgumentParser(description="台股策略變體回測（門檻來源 / 出場規則）")
    ap.add_argument("--test", choices=("threshold", "exit", "robust", "all"), default="all")
    ap.add_argument("--period", default="5y")
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--eval-years", type=int, default=None)
    args = ap.parse_args()

    hist_all = load_history(args.period)
    universe = load_universe()
    codes = set(universe["code"])
    hist = {k: v for k, v in hist_all.items() if k in codes}
    idx_close = hist_all["0050"]["Close"].dropna()
    print(f"[+] 股票池 {len(hist)} 檔；指數代理 0050 {idx_close.index[0].date()} ~ {idx_close.index[-1].date()}")

    screens = build_screens(hist, universe, args.step, _cache_path(args.period, args.step))
    if args.test in ("threshold", "all"):
        expo = exposure_series_offline(hist, idx_close)
        test_threshold(hist, screens, idx_close, expo, args.eval_years)
    if args.test in ("exit", "all"):
        test_exit(hist, screens, idx_close)
    if args.test == "robust":
        test_exit(hist, screens, idx_close, ROBUST_VARIANTS)
    print("\n  註：股票池含存活者偏誤，只比較變體間相對差異；採納門檻見檔頭。")


if __name__ == "__main__":
    main()
