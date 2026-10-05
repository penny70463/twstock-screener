# -*- coding: utf-8 -*-
"""破底追擊做空回測（Claude 設計版，2026-07-05）

設計動機：
    梯隊 3 做空（screen_short.py 因子）經 backtest_short.py 驗證，
    固定出場與移動出場皆為負期望（見 data/backtests/ 對照檔）。
    本腳本改用「破底追擊」——族群突破多頭策略（screen_breakout，
    3年回測期望 +20.4%/波段）的空方鏡像——檢驗順勢進場假設。

方法紀律（防過擬合）：
    - 所有參數在首跑前宣告（鏡射自已驗證的多頭突破參數），跑一次即回報
    - 不在同一資料集上迭代調參；若要調參，需改用訓練/驗證分段
    - regime 閘門與股票池沿用 backtest_short.py，隔離「進出場設計」單一變數

策略規則：
    閘門：TWII regime 為 mixed/bearish 才進場（同 backtest_short）
    進場訊號（訊號日全部成立）：
      1. 收盤創 60 日新低（破底）
      2. 成交量 >= 20 日均量 × 1.5
      3. 收盤 < MA60 且 MA60 < MA120（趨勢確立）
      4. 收盤價 >= 10 元、20 日均成交值 >= 5000 萬（流動性）
    執行：隔日開盤市價放空；持倉期間同檔不加倉
    出場（收盤確認）：
      出場線 = min(進場 × 1.08, 波段最低收盤 × 1.12)，收盤站上即回補
      40 個交易日未觸線 → 收盤強制回補（時間出場）

已知偏誤（解讀時注意）：
    - 存活者偏誤：universe 為今日股票池（做空報酬偏低估）
    - 未模擬券源、借券費、停券強制回補、平盤下放空限制（報酬偏高估）

用法：
    python tests/backtest_short_breakdown.py [--start 2021-01-01] [--end 2026-06-30]
輸出：data/backtests/backtest_short_breakdown_<start>_<end>.json
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# ── 參數（首跑前宣告，禁止事後調整；調參需另開訓練/驗證流程）──
BREAK_DAYS = 60        # 破底：收盤創 N 日新低
VOL_MULT = 1.5         # 量能：20 日均量倍數
MIN_PRICE = 10.0       # 最低股價
MIN_TURNOVER = 5e7     # 20 日均成交值下限（元）
EXIT_STOP = 0.08       # 初始停損：進場價往上 8%
EXIT_TRAIL = 0.12      # 移動停利：波段最低收盤往上 12%
MAX_HOLD = 40          # 最長持倉（交易日），到期收盤回補


def build_regime_series(start: dt.date, end: dt.date) -> dict:
    """同 backtest_short：63/252MA 判定，一次下載全區間（含 550 日暖機）"""
    twii = yf.download("^TWII", start=start - dt.timedelta(days=550),
                       end=end + dt.timedelta(days=1), progress=False)
    if isinstance(twii.columns, pd.MultiIndex):
        twii.columns = twii.columns.get_level_values(0)
    close = twii["Close"]
    ma63 = close.rolling(63).mean()
    ma252 = close.rolling(252).mean()
    out = {}
    for i, ts in enumerate(close.index):
        d = ts.date()
        if d < start or d > end:
            continue
        c, m63, m252 = close.iloc[i], ma63.iloc[i], ma252.iloc[i]
        if pd.isna(m252):
            out[d] = "unknown"
        elif c > m63 > m252:
            out[d] = "bullish"
        elif m63 > c > m252:
            out[d] = "mixed"
        else:
            out[d] = "bearish"
    return out


def load_universe() -> dict:
    with open(REPO / "data" / "results" / "universe_tw.json", encoding="utf-8") as f:
        stocks = json.load(f)["stocks"]
    suffix = {"上市": ".TW", "上櫃": ".TWO"}
    return {s["stock_id"] + suffix.get(s.get("市場", "上市"), ".TW"): s["stock_name"]
            for s in stocks if s.get("stock_id")}


def run(start: dt.date, end: dt.date) -> dict:
    print(f"[回測] 破底追擊做空 ({start} ~ {end})")
    print(f"   破底 {BREAK_DAYS} 日新低｜量 {VOL_MULT}x｜"
          f"出場線 min(進場×{1+EXIT_STOP:.2f}, 最低收盤×{1+EXIT_TRAIL:.2f})｜上限 {MAX_HOLD} 日\n")

    regime = build_regime_series(start, end)
    gated_days = sum(1 for v in regime.values() if v in ("mixed", "bearish"))
    print(f"[OK] regime 序列 {len(regime)} 日（可進場 {gated_days} 日）")
    if not regime or gated_days == 0:
        print("[-] regime 序列異常，中止")
        return {"error": "regime empty"}

    tickers = load_universe()
    print(f"[下載] {len(tickers)} 檔歷史數據（批次）...")
    raw = yf.download(list(tickers), start=start - dt.timedelta(days=200),
                      end=end + dt.timedelta(days=1), group_by="ticker",
                      auto_adjust=True, progress=False, threads=False)
    prices = {}
    for tk in tickers:
        try:
            df = raw[tk].dropna(subset=["Close"])
        except (KeyError, TypeError):
            continue
        if len(df) >= 80:
            prices[tk] = df
    print(f"[OK] {len(prices)} 檔可用\n")
    if not prices:
        return {"error": "no price history"}

    trades, unresolved = [], 0
    open_until = {}

    for tk, df in prices.items():
        close = df["Close"]
        vol = df["Volume"]
        ma60 = close.rolling(60).mean()
        ma120 = close.rolling(120).mean()
        vol20 = vol.rolling(20).mean()
        turnover20 = (close * vol).rolling(20).mean()
        low60 = close.rolling(BREAK_DAYS).min()

        for i in range(BREAK_DAYS, len(df) - 1):
            d = df.index[i].date()
            if d < start or d > end:
                continue
            if regime.get(d) not in ("mixed", "bearish"):
                continue
            if tk in open_until and d <= open_until[tk]:
                continue

            c = float(close.iloc[i])
            # 進場四條件
            if c > float(low60.iloc[i]) + 1e-9:          # 1. 破底（收盤=60日最低收盤）
                continue
            if pd.isna(vol20.iloc[i]) or vol.iloc[i] < VOL_MULT * vol20.iloc[i]:  # 2. 出量
                continue
            m60, m120 = ma60.iloc[i], ma120.iloc[i]
            if pd.isna(m120) or not (c < m60 < m120):     # 3. 趨勢確立
                continue
            if c < MIN_PRICE or pd.isna(turnover20.iloc[i]) or turnover20.iloc[i] < MIN_TURNOVER:
                continue                                   # 4. 價格與流動性

            # 隔日開盤進場
            entry = float(df["Open"].iloc[i + 1])
            if not entry or entry <= 0 or pd.isna(entry):
                continue

            exit_price = exit_date = reason = None
            trough = None
            for j in range(i + 1, len(df)):
                cj = float(close.iloc[j])
                trough = cj if trough is None else min(trough, cj)
                line = min(entry * (1 + EXIT_STOP), trough * (1 + EXIT_TRAIL))
                if cj > line:
                    exit_price, exit_date = cj, df.index[j].date()
                    reason = "移動停利" if cj < entry else "停損"
                    break
                if j - i >= MAX_HOLD:
                    exit_price, exit_date = cj, df.index[j].date()
                    reason = "時間出場"
                    break

            if exit_price is None:
                open_until[tk] = end
                unresolved += 1
                continue

            open_until[tk] = exit_date
            pnl = (entry - exit_price) / entry
            trades.append({
                "code": tk, "entry_date": df.index[i + 1].date().isoformat(),
                "entry": round(entry, 2), "exit_date": exit_date.isoformat(),
                "exit": round(exit_price, 2), "pnl_pct": round(pnl, 4),
                "hold_days": j - i, "reason": reason,
            })

    # ── 統計 ──
    if not trades:
        print("[-] 無任何交易")
        return {"error": "no trades"}

    rets = np.array([t["pnl_pct"] for t in trades])
    years = sorted({t["entry_date"][:4] for t in trades})
    annual = {}
    for y in years:
        r = np.array([t["pnl_pct"] for t in trades if t["entry_date"].startswith(y)])
        annual[y] = {"trades": len(r), "win_rate": float((r > 0).mean()),
                     "avg_return": float(r.mean()), "max_loss": float(r.min()),
                     "max_gain": float(r.max())}

    reasons = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1

    result = {
        "strategy": "breakdown_momentum_short",
        "params": {"break_days": BREAK_DAYS, "vol_mult": VOL_MULT,
                   "min_price": MIN_PRICE, "min_turnover": MIN_TURNOVER,
                   "exit_stop": EXIT_STOP, "exit_trail": EXIT_TRAIL,
                   "max_hold": MAX_HOLD, "entry_style": "next_open",
                   "start": start.isoformat(), "end": end.isoformat()},
        "overall": {"trades": len(rets), "win_rate": float((rets > 0).mean()),
                    "avg_return": float(rets.mean()), "median_return": float(np.median(rets)),
                    "max_loss": float(rets.min()), "max_gain": float(rets.max()),
                    "avg_hold_days": float(np.mean([t["hold_days"] for t in trades])),
                    "unresolved": unresolved, "exit_reasons": reasons},
        "annual": annual,
        "biases": "存活者偏誤（低估）；未模擬券源/借券費/停券/平盤下限制（高估）",
    }

    print("=" * 60)
    o = result["overall"]
    print(f"總交易 {o['trades']} 筆｜勝率 {o['win_rate']:.1%}｜平均 {o['avg_return']:.2%}｜"
          f"中位 {o['median_return']:.2%}")
    print(f"最大獲利 {o['max_gain']:.2%}｜最大虧損 {o['max_loss']:.2%}｜"
          f"平均持倉 {o['avg_hold_days']:.1f} 日｜未平倉 {unresolved}")
    print(f"出場分佈 {reasons}")
    for y, a in annual.items():
        print(f"  {y}: {a['trades']:4d} 筆  勝率 {a['win_rate']:.1%}  平均 {a['avg_return']:+.2%}")
    print("=" * 60)

    out_dir = REPO / "data" / "backtests"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"backtest_short_breakdown_{start}_{end}.json"
    out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
    print(f"[OK] 結果已儲存至: {out_file}")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2026-06-30")
    a = ap.parse_args()
    run(dt.date.fromisoformat(a.start), dt.date.fromisoformat(a.end))
