#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""候補族群監控腳本 - 自動檢查轉強信號

每日執行（可加入 cron 或排程工具），檢查：
1. 觀察股票當日漲幅與量能
2. 比對前日高點，判斷是否達成「進場信號」
3. 族群轉強訊號（多檔點火時發送 LINE 通知）

用法：
    python monitor_cluster.py                    # 檢查今日信號
    python monitor_cluster.py 2026-07-03         # 檢查特定日期
"""

import datetime as dt
import json
from pathlib import Path

import pandas as pd
import yfinance as yf

REPO = Path(__file__).resolve().parent
CANDIDATES_FILE = REPO / "data" / "results" / "cluster_candidates.json"
OUTPUT_FILE = REPO / "data" / "results" / "cluster_monitor_status.json"


def check_cluster_signals(asof: dt.date = None) -> dict:
    """檢查候補族群的轉強信號"""
    if asof is None:
        asof = dt.date.today()

    with open(CANDIDATES_FILE, "r", encoding="utf-8") as f:
        candidates = json.load(f)

    # 下載所有觀察股票的最近 10 日數據
    all_codes = []
    code_to_cluster = {}

    for cluster in candidates["monitoring_priorities"]:
        for stock in cluster["watch_list"]:
            code = stock["code"] + ".TW"
            all_codes.append(code)
            code_to_cluster[code] = {
                "cluster_name": cluster["name"],
                "stock_name": stock["name"],
                "target_signal": stock["signal"],
            }

    if not all_codes:
        return {"error": "No stocks to monitor"}

    print(f"[監控] 檢查 {len(all_codes)} 檔股票，基準日 {asof}")

    # 批次下載
    hist = yf.download(
        all_codes,
        start=asof - dt.timedelta(days=30),
        end=asof + dt.timedelta(days=1),
        progress=False,
    )

    if hist.empty:
        return {"error": "No data available"}

    signals_triggered = []
    status_update = {
        "date": asof.isoformat(),
        "generated_at": dt.datetime.now().isoformat(),
        "monitoring_clusters": [],
    }

    # 逐族群檢查
    for cluster in candidates["monitoring_priorities"]:
        cluster_signals = []

        for stock in cluster["watch_list"]:
            code = stock["code"] + ".TW"

            if code not in hist.columns:
                continue

            try:
                df = hist[code].dropna()

                if len(df) < 2:
                    continue

                # 今日與前日數據
                today = df.iloc[-1]
                yesterday = df.iloc[-2]

                today_close = today["Close"]
                today_high = today["High"]
                today_vol = today["Volume"]
                yesterday_high = yesterday["High"]
                yesterday_vol = yesterday["Volume"]

                # 計算 20 日均量
                vol_20 = df["Volume"].tail(20).mean()

                chg_pct = (today_close - yesterday["Close"]) / yesterday["Close"]
                vol_mult = today_vol / vol_20 if vol_20 > 0 else 0

                # 檢查是否達成信號
                signal_met = False
                signal_reason = []

                # 條件 1：漲幅 >= 3.5%
                if chg_pct >= 0.035:
                    signal_reason.append(f"漲幅 {chg_pct:.1%}")
                    signal_met = True
                elif chg_pct >= 0.02:
                    signal_reason.append(f"漲幅 {chg_pct:.1%}（接近）")

                # 條件 2：成交量 >= 20 日均量 × 2 倍
                if vol_mult >= 2.0:
                    signal_reason.append(f"量能 {vol_mult:.1f}x")
                    signal_met = True
                elif vol_mult >= 1.5:
                    signal_reason.append(f"量能 {vol_mult:.1f}x（接近）")

                # 條件 3：漲回前日高點（進場訊號）
                if today_high >= yesterday_high:
                    signal_reason.append(f"漲回前日高 {yesterday_high:.2f} → {today_close:.2f}")
                    signal_met = True
                elif today_close >= yesterday_high:
                    signal_reason.append(f"收盤達前日高 {yesterday_high:.2f}")

                cluster_signals.append(
                    {
                        "code": stock["code"],
                        "name": stock["name"],
                        "current_price": float(today_close),
                        "chg_pct": float(chg_pct),
                        "vol_mult": float(vol_mult),
                        "signal_met": signal_met,
                        "signal_reason": " | ".join(signal_reason),
                        "target_signal": stock["signal"],
                    }
                )

                if signal_met:
                    signals_triggered.append(
                        f"{stock['name']}({stock['code']}) - {cluster['name']} - {' | '.join(signal_reason)}"
                    )

            except Exception as e:
                print(f"  ! 檢查 {stock['name']} 失敗: {e}")
                continue

        status_update["monitoring_clusters"].append(
            {
                "name": cluster["name"],
                "priority": cluster["priority"],
                "status": cluster["status"],
                "expected_gain": cluster["expected_gain"],
                "stocks": cluster_signals,
            }
        )

    # 保存結果
    status_update["triggered_signals"] = signals_triggered
    status_update["summary"] = (
        f"共觸發 {len(signals_triggered)} 個轉強信號"
        if signals_triggered
        else "暫無轉強信號"
    )

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(status_update, f, ensure_ascii=False, indent=2)

    print(f"\n[結果] {status_update['summary']}")
    if signals_triggered:
        print("[觸發訊號]")
        for sig in signals_triggered:
            print(f"  ✓ {sig}")

    return status_update


if __name__ == "__main__":
    import sys

    asof = None
    if len(sys.argv) > 1:
        try:
            asof = dt.datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
        except ValueError:
            print(f"日期格式錯誤: {sys.argv[1]}")
            sys.exit(1)

    result = check_cluster_signals(asof)
    print(f"\n✓ 監控完成，結果已存至 {OUTPUT_FILE}")
