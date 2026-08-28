"""台股頂部排序依據 A/B 回測：當日漲幅排序 vs 總分排序

背景：
    現行主管線（pipeline.py）對通過門檻的台股先過濾「今日上漲」，
    再按當日漲幅排序取頂部——本質是追單日動能。美股回測已證明
    高周轉排序會損害夏普（納入短期訊號 1.41→1.14）。本腳本用
    生產版評分邏輯（screener.run_screen）逐週歷史重演，比較三種
    頂部排序的前瞻報酬，決定是否改用總分排序。

方法：
    - 資料：src/advisor/cache/hist_2y_*.pkl（與生產同源的 2 年日線）
    - 每 5 個交易日一個評分日，截斷歷史至當日後呼叫 run_screen
      （inst/revenue 缺料，權重自動換算回 100，兩變體條件相同）
    - 變體 A：漲幅>0 過濾 → 按當日漲幅排序取前 10（現行邏輯）
    - 變體 B：漲幅>0 過濾 → 按總分排序取前 10
    - 變體 C：不過濾漲幅 → 按總分排序取前 10
    - 前瞻 5/10/20 日等權報酬與勝率

限制：
    - 門檻依當日大盤狀態用生產值（多頭 70／中性 75／空頭 80）；三變體
      當日共用同一股票池，只比排序
    - 快取股票池為「今日」universe 回溯，含存活者偏誤 → 只比較
      變體間相對差異，不看絕對報酬
    - 大盤狀態用 0050 複製 market.get_regime 規則（季線/半年線 + 斜率）

用法:
    python tests/backtest_tw_ranking.py
    python tests/backtest_tw_ranking.py --period 5y --top 10 --step 5
    python tests/backtest_tw_ranking.py --period 5y --eval-years 3
"""
import argparse
import glob
import sys
sys.path.insert(0, ".")

import json
import pickle

import numpy as np
import pandas as pd

from src.advisor import config, screener

HORIZONS = (5, 10, 20)
MIN_BARS = 130
WARMUP_BARS = 260  # 52 週高低點與 MA240 需要的最少歷史
REGIMES = ("多頭", "中性", "空頭")
SCORE_BY_REGIME = {
    "多頭": config.SCORE_BULL,
    "中性": config.SCORE_NEUTRAL,
    "空頭": config.SCORE_BEAR,
}


def load_history(period: str = "2y") -> dict[str, pd.DataFrame]:
    paths = sorted(glob.glob(f"src/advisor/cache/hist_{period}_*.pkl"))
    if not paths:
        raise FileNotFoundError(
            f"找不到 src/advisor/cache/hist_{period}_*.pkl —— 先下載該年期日線")
    path = paths[-1]
    print(f"[>] 載入價格快取: {path}")
    hist = pickle.load(open(path, "rb"))
    hist = {
        k: df for k, df in hist.items()
        if isinstance(df, pd.DataFrame) and not df.empty
        and "Close" in df.columns and len(df) >= MIN_BARS
    }
    print(f"[+] 有效檔數: {len(hist)}")
    return hist


def load_universe() -> pd.DataFrame:
    u = json.load(open("data/results/universe_tw.json"))["stocks"]
    return pd.DataFrame([
        {"code": s["stock_id"], "name": s.get("stock_name", ""),
         "market": s.get("市場", ""), "industry": s.get("industry_category", "")}
        for s in u
    ])


def forward_return(df: pd.DataFrame, on_date, horizon: int) -> float | None:
    """D 收盤 → D+h 收盤的報酬；資料不足回傳 None。"""
    try:
        pos = df.index.get_loc(on_date)
    except KeyError:
        return None
    if pos + horizon >= len(df):
        return None
    return float(df["Close"].iloc[pos + horizon] / df["Close"].iloc[pos] - 1)


def regime_on(close: pd.Series, d) -> str:
    """與 market.get_regime 同一套均線規則，用 0050 收盤。"""
    sub = close.loc[:d].dropna()
    if len(sub) < 130:
        return "未知"
    ma60 = sub.rolling(60).mean()
    ma120 = sub.rolling(120).mean()
    c = float(sub.iloc[-1])
    m60, m120 = float(ma60.iloc[-1]), float(ma120.iloc[-1])
    m60_prev = float(ma60.iloc[-10])
    if any(np.isnan(x) for x in (c, m60, m120, m60_prev)):
        return "未知"
    if c > m60 > m120 and m60 > m60_prev:
        return "多頭"
    if c < m120 and c < m60:
        return "空頭"
    return "中性"


def _stats(arr: np.ndarray) -> tuple[float, float, float, float]:
    """mean%, hit%, std%, t"""
    if len(arr) == 0:
        return (float("nan"), float("nan"), float("nan"), float("nan"))
    t = arr.mean() / (arr.std(ddof=1) / np.sqrt(len(arr))) if len(arr) > 1 else 0.0
    return (arr.mean() * 100, (arr > 0).mean() * 100, arr.std(ddof=1) * 100, t)


def _print_block(title: str, port_rets: dict, lifts: dict, n_dates: int, top_n: int) -> None:
    labels = {"A": f"A 漲幅排序 Top{top_n}（現行）",
              "B": f"B 總分排序 Top{top_n}（漲幅>0 過濾）",
              "C": f"C 總分排序 Top{top_n}（不過濾漲幅）"}
    print(f"\n{'='*78}")
    print(f"  {title}（樣本 {n_dates} 期）")
    print(f"{'='*78}")
    for h in HORIZONS:
        print(f"\n  ── 前瞻 {h} 日 ──")
        print(f"  {'變體':<32s} {'平均報酬':>8s} {'命中P':>7s} {'超額P−U':>8s} {'t值':>6s}")
        for v in ("A", "B", "C"):
            r = np.array(port_rets[v][h])
            if len(r) == 0:
                continue
            mean, hit, _sd, t = _stats(r)
            lf = np.array(lifts[v][h])
            lift = lf.mean() * 100 if len(lf) else float("nan")
            print(f"  {labels[v]:<32s} {mean:>7.2f}% {hit:>6.1f}% {lift:>+7.1f}pp {t:>6.2f}")


def run(top_n: int, step: int, period: str = "2y", eval_years: int | None = None) -> None:
    hist_all = load_history(period)
    universe = load_universe()
    uni_codes = set(universe["code"].tolist())
    # 只留股票池 + 0050，加速 run_screen、避開美股混入
    hist = {k: v for k, v in hist_all.items() if k in uni_codes}
    idx = hist_all.get("0050")
    if idx is None or "Close" not in idx.columns:
        raise RuntimeError("快取沒有 0050，無法標大盤狀態")
    idx_close = idx["Close"]
    print(f"[+] 回測股票池 {len(hist)} 檔（universe {len(uni_codes)}）")

    calendar = max((df.index for df in hist.values()), key=len)
    max_h = max(HORIZONS)
    eval_positions = range(WARMUP_BARS, len(calendar) - max_h, step)
    eval_dates = [calendar[i] for i in eval_positions]
    if eval_years:
        cutoff = pd.Timestamp(eval_dates[-1]) - pd.DateOffset(years=eval_years)
        eval_dates = [d for d in eval_dates if d >= cutoff]
        print(f"[>] 只評最近 {eval_years} 年：{len(eval_dates)} 個評分日"
              f"（{eval_dates[0].date()} ~ {eval_dates[-1].date()}）\n")
    else:
        print(f"[>] 評分日 {len(eval_dates)} 個（{eval_dates[0].date()} ~ {eval_dates[-1].date()}，每 {step} 日）\n")

    def empty_store():
        return {v: {h: [] for h in HORIZONS} for v in ("A", "B", "C")}

    overall_rets = empty_store()
    overall_lift = empty_store()
    by_reg_rets = {rg: empty_store() for rg in REGIMES}
    by_reg_lift = {rg: empty_store() for rg in REGIMES}
    by_reg_n = {rg: 0 for rg in REGIMES}
    overlaps: list[float] = []
    top3_rets = empty_store()
    top3_lift = empty_store()
    top3_by_reg = {rg: empty_store() for rg in REGIMES}
    top3_by_reg_lift = {rg: empty_store() for rg in REGIMES}

    for n, d in enumerate(eval_dates, 1):
        rg = regime_on(idx_close, d)
        if rg not in SCORE_BY_REGIME:
            continue
        threshold = SCORE_BY_REGIME[rg]

        truncated = {}
        for code, df in hist.items():
            sub = df.loc[:d]
            if len(sub) >= MIN_BARS and sub.index[-1] == d:
                truncated[code] = sub
        if len(truncated) < 100:
            continue

        screened, _ = screener.run_screen(universe, truncated, None, None, threshold)
        if screened.empty:
            continue

        chg = {}
        for code in screened["代號"]:
            sub = truncated[code]
            if len(sub) >= 2:
                chg[code] = float(sub["Close"].iloc[-1] / sub["Close"].iloc[-2] - 1)
        screened = screened.assign(chg=screened["代號"].map(chg)).dropna(subset=["chg"])
        up = screened[screened["chg"] > 0]

        picks = {
            "A": up.sort_values("chg", ascending=False).head(top_n)["代號"].tolist(),
            "B": up.sort_values("總分", ascending=False).head(top_n)["代號"].tolist(),
            "C": screened.sort_values("總分", ascending=False).head(top_n)["代號"].tolist(),
        }
        if picks["A"] and picks["B"]:
            overlaps.append(len(set(picks["A"]) & set(picks["B"])) / max(len(picks["A"]), 1))

        by_reg_n[rg] += 1
        univ_fwd = {h: [] for h in HORIZONS}
        for code, df in hist.items():
            for h in HORIZONS:
                r = forward_return(df, d, h)
                if r is not None:
                    univ_fwd[h].append(r)

        for v, codes in picks.items():
            for h in HORIZONS:
                rets = [r for c in codes
                        if (r := forward_return(hist[c], d, h)) is not None]
                if not rets or not univ_fwd[h]:
                    continue
                mean_r = float(np.mean(rets))
                p_hit = float(np.mean([x > 0 for x in rets]))
                u_hit = float(np.mean([x > 0 for x in univ_fwd[h]]))
                overall_rets[v][h].append(mean_r)
                overall_lift[v][h].append(p_hit - u_hit)
                by_reg_rets[rg][v][h].append(mean_r)
                by_reg_lift[rg][v][h].append(p_hit - u_hit)

            codes3 = codes[:3]
            for h in HORIZONS:
                rets = [r for c in codes3
                        if (r := forward_return(hist[c], d, h)) is not None]
                if not rets or not univ_fwd[h]:
                    continue
                mean_r = float(np.mean(rets))
                p_hit = float(np.mean([x > 0 for x in rets]))
                u_hit = float(np.mean([x > 0 for x in univ_fwd[h]]))
                top3_rets[v][h].append(mean_r)
                top3_lift[v][h].append(p_hit - u_hit)
                top3_by_reg[rg][v][h].append(mean_r)
                top3_by_reg_lift[rg][v][h].append(p_hit - u_hit)

        if n % 10 == 0:
            print(f"  ... {n}/{len(eval_dates)} 個評分日完成")

    n_all = len(overall_rets["A"][HORIZONS[0]])
    print(f"\n  A/B 頂部重疊率: 平均 {np.mean(overlaps)*100:.0f}%")
    print(f"  狀態樣本: " + "、".join(f"{rg} {by_reg_n[rg]} 期" for rg in REGIMES))

    _print_block(f"全樣本 Top{top_n}（門檻隨狀態 70/75/80）", overall_rets, overall_lift, n_all, top_n)
    _print_block(f"全樣本 Top3（同排序取前 3）", top3_rets, top3_lift, len(top3_rets["A"][5]), 3)

    for rg in REGIMES:
        if by_reg_n[rg] == 0:
            continue
        _print_block(f"{rg} Top{top_n}", by_reg_rets[rg], by_reg_lift[rg], by_reg_n[rg], top_n)
        _print_block(f"{rg} Top3", top3_by_reg[rg], top3_by_reg_lift[rg],
                     len(top3_by_reg[rg]["A"][5]), 3)

    print(f"\n  裁決用：中性 Top3／Top{top_n} 的 T+5，C（或 B）是否報酬與 P−U 都贏 A。")
    print(f"  多頭即使 C 小贏也不改。空頭樣本太少則維持 A、靠曝險縮部位。")
    print(f"  註：股票池含存活者偏誤，僅比較變體間相對差異。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="台股頂部排序 A/B 回測")
    parser.add_argument("--top", type=int, default=10, help="頂部檔數")
    parser.add_argument("--step", type=int, default=5, help="評分間隔（交易日）")
    parser.add_argument("--period", default="2y", help="快取年期（2y/3y/5y）")
    parser.add_argument("--eval-years", type=int, default=None,
                        help="只評最近 N 年的評分日（用更長快取做 3 年對照）")
    args = parser.parse_args()
    run(args.top, args.step, period=args.period, eval_years=args.eval_years)
