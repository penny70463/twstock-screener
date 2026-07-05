# -*- coding: utf-8 -*-
"""事件驅動選股（展覽會 + 法說會前後供應鏈股）

回測結論（待補充）：
- 展覽會前 10～15 日進場供應鏈股，出場規則待定
- 法說會前 5～10 日進場龍頭股及供應鏈股

用法：
    python screen_event_driven.py [YYYY-MM-DD]

輸出：data/results/event_driven_tw.json + CSV
相依：pandas yfinance src.market_regime src.events
"""
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

from src.market_regime import get_regime, is_regime_allowed
from src.events import load_events_calendar, get_supply_chain
from src.advisor.indicators import sma

REPO = Path(__file__).resolve().parent
ASOF = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") \
    else dt.date.today().isoformat()
OUT_JSON = REPO / "data" / "results" / "event_driven_tw.json"

LOOKBACK = 14  # 往後查看 14 天內的展覽會


def main() -> None:
    asof = dt.date.fromisoformat(ASOF)
    regime = get_regime(asof)

    payload = {
        "date": ASOF,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "regime": regime,
        "params": {
            "lookback_days": LOOKBACK,
            "regime_filter": "多頭、混合 Regime 時執行",
        },
        "events": [],
    }

    # Regime 篩選：非多頭/混合時跳過
    if not is_regime_allowed(regime, "long"):
        payload["note"] = f"當前 Regime={regime}，不執行多頭選股"
        OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print(f"Regime={regime}，跳過事件驅動選股")
        return

    # 載入展覽會日期庫 + 股票池
    cal = load_events_calendar()
    with open(REPO / "data" / "results" / "universe_tw.json", encoding="utf-8") as f:
        uni = {s["stock_id"]: s for s in json.load(f)["stocks"]}

    # 篩選即將到來的展覽會（簡化版：以月份判定）
    upcoming_exhibitions = []
    for exh in cal.get("exhibitions", []):
        season = exh.get("season")
        if isinstance(season, list):
            seasons = season
        else:
            seasons = [season]

        for s in seasons:
            if asof.month == s or (asof.month < s <= asof.month + (LOOKBACK // 30)):
                upcoming_exhibitions.append(exh)
                break

    if not upcoming_exhibitions:
        payload["note"] = f"未來 {LOOKBACK} 天內無主要展覽會"
        OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print(f"未來 {LOOKBACK} 天內無主要展覽會")
        return

    # 下載股價（所有相關股票）
    related_stocks = set()
    for exh in upcoming_exhibitions:
        related_stocks.update(exh.get("key_stocks", []))
        related_stocks.update(exh.get("supply_chain", {}).keys())
        for chain in exh.get("supply_chain", {}).values():
            related_stocks.update(chain)

    related_stocks = [s for s in related_stocks if s in uni]
    print(f"下載 {len(related_stocks)} 檔相關股票日線 ...", flush=True)

    tickers = {c: c + ".TW" for c in related_stocks}
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

    # 篩選每個展覽會的相關股票
    for exh in upcoming_exhibitions:
        event_name = exh.get("name_zh")
        key_stocks = exh.get("key_stocks", [])
        supply_chain_map = exh.get("supply_chain", {})

        screened = []

        # 龍頭股
        for stock_id in key_stocks:
            if stock_id not in prices:
                continue
            df = prices[stock_id]
            if len(df) < 60:
                continue

            close = float(df.iloc[-1]["Close"])
            ma60 = float(sma(df["Close"], 60).iloc[-1])
            ma120 = float(sma(df["Close"], 120).iloc[-1])

            # 簡易篩選：收盤 > 季線 > 120 日線
            if close > ma60 > ma120:
                screened.append({
                    "stock_id": stock_id,
                    "stock_name": uni[stock_id].get("stock_name", ""),
                    "type": "龍頭",
                    "current_price": round(close, 2),
                    "ma60": round(ma60, 2),
                    "dist_ma60_pct": round((close / ma60 - 1) * 100, 1),
                })

        # 供應鏈股
        for stock_id, chain_stocks in supply_chain_map.items():
            if stock_id not in prices:
                continue

            for chain_stock in chain_stocks:
                if chain_stock not in prices:
                    continue

                df = prices[chain_stock]
                if len(df) < 60:
                    continue

                close = float(df.iloc[-1]["Close"])
                ma60 = float(sma(df["Close"], 60).iloc[-1])
                ma120 = float(sma(df["Close"], 120).iloc[-1])

                if close > ma60 > ma120:
                    screened.append({
                        "stock_id": chain_stock,
                        "stock_name": uni.get(chain_stock, {}).get("stock_name", ""),
                        "type": "供應鏈",
                        "current_price": round(close, 2),
                        "ma60": round(ma60, 2),
                        "dist_ma60_pct": round((close / ma60 - 1) * 100, 1),
                    })

        screened = list({s["stock_id"]: s for s in screened}.values())  # 去重
        screened.sort(key=lambda s: -s.get("dist_ma60_pct", 0))

        if screened:
            payload["events"].append({
                "name": event_name,
                "season": exh.get("season"),
                "lookback_days": exh.get("lookback_days", 10),
                "count": len(screened),
                "stocks": screened[:10],  # 前 10 檔
            })

    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"事件驅動選股完成 → {OUT_JSON}")

    if payload.get("events"):
        csv = REPO / "data" / "results" / f"screen_event_driven_result_{ASOF.replace('-', '')}.csv"
        rows = []
        for evt in payload["events"]:
            for s in evt.get("stocks", []):
                rows.append({
                    "event": evt["name"],
                    "stock_id": s["stock_id"],
                    "stock_name": s["stock_name"],
                    "type": s["type"],
                    "current_price": s["current_price"],
                    "ma60": s["ma60"],
                    "dist_ma60_pct": s["dist_ma60_pct"],
                })
        pd.DataFrame(rows).to_csv(csv, index=False, encoding="utf-8-sig")
        print(f"已存檔：{csv}")


if __name__ == "__main__":
    main()
