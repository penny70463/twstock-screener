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
    - 門檻固定 70（未含大盤動態門檻；三變體共用同一股票池，比較公平）
    - 快取股票池為「今日」универse 回溯，含存活者偏誤 → 只比較
      變體間相對差異，不看絕對報酬

用法:
    python tests/backtest_tw_ranking.py
    python tests/backtest_tw_ranking.py --top 10 --step 5
"""
import argparse
import glob
import sys
sys.path.insert(0, ".")

import json
import pickle

import numpy as np
import pandas as pd

from src.advisor import screener

HORIZONS = (5, 10, 20)
THRESHOLD = 70
MIN_BARS = 130
WARMUP_BARS = 260  # 52 週高低點與 MA240 需要的最少歷史


def load_history() -> dict[str, pd.DataFrame]:
    paths = sorted(glob.glob("src/advisor/cache/hist_2y_*.pkl"))
    if not paths:
        raise FileNotFoundError("找不到 src/advisor/cache/hist_2y_*.pkl")
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


def run(top_n: int, step: int) -> None:
    hist = load_history()
    universe = load_universe()

    # 主日曆：用最長的個股索引當交易日參考（台積電幾乎不停牌）
    calendar = max((df.index for df in hist.values()), key=len)
    max_h = max(HORIZONS)
    eval_positions = range(WARMUP_BARS, len(calendar) - max_h, step)
    eval_dates = [calendar[i] for i in eval_positions]
    print(f"[>] 評分日 {len(eval_dates)} 個（{eval_dates[0].date()} ~ {eval_dates[-1].date()}，每 {step} 日）\n")

    # 每變體 × 每期限：收集每個評分日的等權組合報酬
    port_rets: dict[str, dict[int, list[float]]] = {
        v: {h: [] for h in HORIZONS} for v in ("A", "B", "C")}
    overlaps: list[float] = []

    for n, d in enumerate(eval_dates, 1):
        truncated = {}
        for code, df in hist.items():
            sub = df.loc[:d]
            if len(sub) >= MIN_BARS and sub.index[-1] == d:
                truncated[code] = sub
        if len(truncated) < 100:
            continue

        screened, _ = screener.run_screen(universe, truncated, None, None, THRESHOLD)
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

        for v, codes in picks.items():
            for h in HORIZONS:
                rets = [r for c in codes
                        if (r := forward_return(hist[c], d, h)) is not None]
                if rets:
                    port_rets[v][h].append(float(np.mean(rets)))

        if n % 10 == 0:
            print(f"  ... {n}/{len(eval_dates)} 個評分日完成")

    # 報表
    labels = {"A": f"A 漲幅排序 Top{top_n}（現行）",
              "B": f"B 總分排序 Top{top_n}（漲幅>0 過濾）",
              "C": f"C 總分排序 Top{top_n}（不過濾漲幅）"}
    print(f"\n{'='*72}")
    print(f"  台股頂部排序 A/B 回測（門檻 {THRESHOLD}、每 {step} 日評分、樣本 {len(port_rets['A'][HORIZONS[0]])} 期）")
    print(f"{'='*72}")
    print(f"  A/B 頂部重疊率: 平均 {np.mean(overlaps)*100:.0f}%")
    for h in HORIZONS:
        print(f"\n  ── 前瞻 {h} 日 ──")
        print(f"  {'變體':<28s} {'平均報酬':>8s} {'勝率':>7s} {'標準差':>7s} {'t值':>6s}")
        for v in ("A", "B", "C"):
            r = np.array(port_rets[v][h])
            if len(r) == 0:
                continue
            t = r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if len(r) > 1 else 0
            print(f"  {labels[v]:<28s} {r.mean()*100:>7.2f}% {(r > 0).mean()*100:>6.1f}% "
                  f"{r.std(ddof=1)*100:>6.2f}% {t:>6.2f}")
    print(f"\n  註：股票池含存活者偏誤，僅比較變體間相對差異。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="台股頂部排序 A/B 回測")
    parser.add_argument("--top", type=int, default=10, help="頂部檔數")
    parser.add_argument("--step", type=int, default=5, help="評分間隔（交易日）")
    args = parser.parse_args()
    run(args.top, args.step)
