# -*- coding: utf-8 -*-
"""做空選股（空頭 / 混合 Regime 下的放空機會）

回測結論（待補充）：
- 當指數跌破季線時，符合 4 條做空條件的股票進場放空
- 停利 -3% ～ -5%（做空止盈），停損 +8% ～ +10%（做空止損）

用法：
    python screen_short.py [YYYY-MM-DD]

輸出：data/results/short_tw.json + CSV
相依：pandas yfinance src.market_regime src.advisor.margin_futures
"""
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

from src.market_regime import get_regime, is_regime_allowed
from src.advisor.margin_futures import fetch_margin_cached
from src.advisor.indicators import sma
from src.advisor.data import fetch_revenue

REPO = Path(__file__).resolve().parent
ASOF = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") \
    else dt.date.today().isoformat()
OUT_JSON = REPO / "data" / "results" / "short_tw.json"

# 做空參數
SHORT_STOP_PROFIT = 0.03    # 停利：下跌 3%
SHORT_STOP_LOSS = 0.10      # 停損：上漲 10%
MIN_PULLBACK = 0.03         # 從季線跌破最少 3%


def main() -> None:
    asof = dt.date.fromisoformat(ASOF)
    regime = get_regime(asof)

    payload = {
        "date": ASOF,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "regime": regime,
        "params": {
            "stop_profit_pct": SHORT_STOP_PROFIT,
            "stop_loss_pct": SHORT_STOP_LOSS,
            "min_pullback": MIN_PULLBACK,
            "short_allowed": is_regime_allowed(regime, "short"),
        },
        "stocks": [],
    }

    # Regime 篩選：只在 mixed / bearish 時執行做空
    if not is_regime_allowed(regime, "short"):
        payload["note"] = f"當前 Regime={regime}，不執行做空選股"
        OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print(f"Regime={regime}，跳過做空選股")
        return

    # 載入股票池
    with open(REPO / "data" / "results" / "universe_tw.json", encoding="utf-8") as f:
        uni = {s["stock_id"]: s for s in json.load(f)["stocks"]}

    codes = list(uni.keys())

    # 下載股價
    print(f"下載 {len(codes)} 檔 2 年日線 ...", flush=True)
    # 上櫃股 yfinance 後綴為 .TWO（同 screen_breakout.py 慣例）
    suffix = {"上市": ".TW", "上櫃": ".TWO"}
    tickers = {c: c + suffix.get(uni[c].get("市場", "上市"), ".TW") for c in codes}
    raw = yf.download(list(tickers.values()), period="2y", group_by="ticker",
                      auto_adjust=True, progress=False, threads=True)

    prices = {}
    for c, tk in tickers.items():
        try:
            df = raw[tk].dropna(subset=["Close"]).loc[:pd.Timestamp(asof)]
        except (KeyError, TypeError):
            continue
        if len(df) >= 60:
            prices[c] = df

    # 取得融券資料（回溯：無資料就往前推）
    print(f"抓取融券資料...", flush=True)
    margin_df = None
    margin_by_code = {}

    # 最多回溯 5 個交易日
    for backtrack_days in range(0, 5):
        check_date = asof - dt.timedelta(days=backtrack_days + 1)
        if check_date.weekday() >= 5:  # 略過週末
            continue
        margin_df = fetch_margin_cached(check_date, market="TW")
        if margin_df is not None and not margin_df.empty:
            print(f"融券資料來源：{check_date}")
            margin_by_code = {row["code"]: row for _, row in margin_df.iterrows()}
            break

    if not margin_by_code:
        print("警告：無融券資料可用，將以融券餘額為 0 處理", flush=True)

    # 取得營收資料（基本面空頭判定）
    print(f"抓取營收資料...", flush=True)
    revenue_df = fetch_revenue()
    revenue_by_code = {}
    if revenue_df is not None and not revenue_df.empty:
        for _, row in revenue_df.iterrows():
            code = str(row.get("code", "")).strip()
            mom = float(row.get("mom", 0)) if row.get("mom") else 0
            revenue_by_code[code] = {"mom": mom}

    # 篩選做空候選
    stocks_out = []
    for code in codes:
        if code not in prices:
            continue

        df = prices[code]
        if len(df) < 60:
            continue

        close = float(df.iloc[-1]["Close"])
        ma60 = float(sma(df["Close"], 60).iloc[-1])
        ma120 = float(sma(df["Close"], 120).iloc[-1])

        # ── 條件 1：技術面空頭 ──
        # 收盤價跌破季線 (60MA)
        if close >= ma60 * 0.99:  # 容忍 1%
            continue

        # ── 條件 2：籌碼面空頭 ──
        # 融券餘額 > 0
        margin_info = margin_by_code.get(code, {})
        short_balance = float(margin_info.get("short_balance", 0))
        if short_balance <= 0:
            # 融券很少，不考慮
            continue

        # ── 條件 3：基本面空頭 ──
        # 營收月減 > 30%
        revenue_info = revenue_by_code.get(code, {})
        mom = float(revenue_info.get("mom", 0))
        if mom > -30:  # 月減未超過 30%，略過
            continue

        # ── 條件 4：動能確認 ──
        # 近期有明顯下跌（例如最近 1 月內曾創近 60 日新低）
        low_60 = float(df["Low"].iloc[-60:].min())
        current_low = float(df["Low"].iloc[-1])
        # 簡化：當前接近 60 日低點（允許 5% buffer）
        if current_low > low_60 * 1.05:
            continue

        # 計算進場參考 + 停利/停損
        entry_ref = round(ma60 * 0.98, 2)  # 季線往下 2%
        stop_profit = round(entry_ref * (1 - SHORT_STOP_PROFIT), 2)
        stop_loss = round(entry_ref * (1 + SHORT_STOP_LOSS), 2)

        # 當前損益（模擬進場後的 PnL）
        pnl_pct = None
        if entry_ref > 0:
            pnl_pct = round((close / entry_ref - 1) * 100, 1)

        rec = {
            "stock_id": code,
            "stock_name": uni[code].get("stock_name", ""),
            "current_price": round(close, 2),
            "ma60": round(ma60, 2),
            "dist_ma60_pct": round((close / ma60 - 1) * 100, 1),
            "short_balance": round(short_balance),
            "entry_ref": entry_ref,
            "stop_profit": stop_profit,
            "stop_loss": stop_loss,
            "current_pnl_pct": pnl_pct,
        }
        stocks_out.append(rec)

    # 按跌幅排序（最空頭的優先）
    stocks_out.sort(key=lambda r: r.get("dist_ma60_pct", 0))
    stocks_out = stocks_out[:20]  # 前 20 檔

    payload["stocks"] = stocks_out
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"做空選股完成（候選 {len(stocks_out)} 檔）→ {OUT_JSON}")

    if stocks_out:
        csv = REPO / "data" / "results" / f"screen_short_result_{ASOF.replace('-', '')}.csv"
        pd.DataFrame(stocks_out).to_csv(csv, index=False, encoding="utf-8-sig")
        print(f"已存檔：{csv}")


if __name__ == "__main__":
    main()
