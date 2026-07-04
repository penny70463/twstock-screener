# -*- coding: utf-8 -*-
"""集團作帳選股（年底行情）：11 月中進場、年底前出場

回測結論（2016~2025 十個年度、21 集團 78 檔成員股，對照加權指數）：
- 傳統認知的「12 月集團作帳」已被市場搶跑：超額報酬集中在 11/15~11月底
  （+0.51%，8/10 年為正）；12 月各子窗口與隔年 1 月首週皆為負超額
  （12月初~12/20 僅 1/10 年為正）→ 12 月「新進場」歷史上是輸家
- 有效規則：每集團取「今年以來最強 2 檔」且進場時站上 60 日線
  （落後補漲選法無效；強者恆強＋趨勢濾網 α +1.10%、7/10 年為正）
- 出場：12 月最後交易日收盤（絕對報酬最高 +4.7%）；α 高點其實在 11 月底，
  保守者可 11 月底先出一半
- 停損：進場價 -8%（收盤確認；MAE 中位 -4%、P25 -6.9%）
- 警示：2024、2025 連兩年 α 為負（-5.8%/-6.2%），效應可能持續衰減

季節時程（前端依 phase 顯示）：
- 11/01~11/14 preview：預備名單（以最新資料試算，11/14 收盤定案）
- 11/15~年底  active：正式名單，附進場參考價、停損線、出場日
- 其他月份    off：頁籤顯示說明與下一季時間

用法：python screen_group.py [YYYY-MM-DD]
輸出：data/results/group_tw.json + 季節內另存 CSV
相依：pandas yfinance
"""
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

REPO = Path(__file__).resolve().parent
ASOF = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") \
    else dt.date.today().isoformat()
OUT_JSON = REPO / "data" / "results" / "group_tw.json"

STOP_PCT = 0.08      # 停損：進場參考價 -8%（收盤確認）
TOP_PER_GROUP = 2    # 每集團取 YTD 最強 N 檔
ENTRY_MMDD = "11-15"  # 進場：此日期後第一個交易日開盤

# 台灣主要集團上市櫃成員（2026-07 人工核對；金控集團不納入）
GROUPS = {
    "台塑集團": {"1301": "台塑", "1303": "南亞", "1326": "台化", "6505": "台塑化",
              "1434": "福懋", "2408": "南亞科", "3532": "台勝科", "8046": "南電"},
    "鴻海集團": {"2317": "鴻海", "2354": "鴻準", "3481": "群創", "6414": "樺漢",
              "2328": "廣宇", "3413": "京鼎", "3062": "建漢"},
    "遠東集團": {"1402": "遠東新", "1102": "亞泥", "2606": "裕民", "4904": "遠傳",
              "2903": "遠百", "1460": "宏遠", "1710": "東聯"},
    "統一集團": {"1216": "統一", "2912": "統一超", "1232": "大統益"},
    "聯華神通": {"1229": "聯華", "2347": "聯強", "3005": "神基", "3706": "神達投控"},
    "華新集團": {"1605": "華新", "2344": "華邦電", "2492": "華新科",
              "6116": "彩晶", "8110": "華東"},
    "金仁寶集團": {"2312": "金寶", "2324": "仁寶", "3596": "智易", "6282": "康舒"},
    "裕隆集團": {"2201": "裕隆", "2204": "中華", "2227": "裕日車", "9941": "裕融"},
    "台聚集團": {"1304": "台聚", "1305": "華夏", "1308": "亞聚", "1309": "台達化"},
    "長榮集團": {"2603": "長榮", "2618": "長榮航", "2607": "榮運", "2645": "長榮航太"},
    "潤泰集團": {"9945": "潤泰新", "2915": "潤泰全", "2597": "潤弘"},
    "新光集團": {"1409": "新纖", "1419": "新紡", "9925": "新保"},
    "國巨集團": {"2327": "國巨", "2375": "凱美", "6449": "鈺邦"},
    "明基友達": {"2352": "佳世達", "2409": "友達", "8163": "達方", "8215": "明基材"},
    "東元集團": {"1504": "東元", "2321": "東訊", "8249": "菱光"},
    "永豐餘集團": {"1907": "永豐餘", "8069": "元太"},
    "威京集團": {"1314": "中石化", "2515": "中工"},
    "緯創集團": {"3231": "緯創", "6669": "緯穎"},
    "廣達集團": {"2382": "廣達", "6188": "廣明"},
    "和泰集團": {"2207": "和泰車", "6592": "和潤企業"},
    "遠雄集團": {"5522": "遠雄", "5607": "遠雄港"},
}
# 上櫃成員（yfinance 後綴 .TWO），其餘 .TW
TWO_CODES = {"8069", "6188"}


def season_phase(d: dt.date) -> str:
    if d.month == 11:
        return "preview" if d.day < 15 else "active"
    if d.month == 12:
        return "active"
    return "off"


def main() -> None:
    asof = dt.date.fromisoformat(ASOF)
    phase = season_phase(asof)
    year = asof.year

    payload = {
        "date": ASOF,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "phase": phase,
        "params": {"stop_pct": STOP_PCT, "top_per_group": TOP_PER_GROUP,
                   "entry": f"{year}-{ENTRY_MMDD} 後首個交易日開盤",
                   "exit": f"{year}-12 月最後交易日收盤"},
        "stocks": [],
    }
    if phase == "off":
        payload["note"] = "非作帳季節（每年 11/1 起顯示預備名單、11/15 起正式進場）"
        OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print(f"非季節（{ASOF}），已寫入 off 狀態")
        return

    codes = [c for ms in GROUPS.values() for c in ms]
    tickers = {c: c + (".TWO" if c in TWO_CODES else ".TW") for c in codes}
    print(f"下載 {len(codes)} 檔集團股 2 年日線 ...", flush=True)
    raw = yf.download(list(tickers.values()), period="2y", group_by="ticker",
                      auto_adjust=True, progress=False, threads=True)

    prices = {}
    for c, tk in tickers.items():
        try:
            df = raw[tk].dropna(subset=["Close"]).loc[:ASOF]
        except KeyError:
            continue
        if len(df) >= 80:
            prices[c] = df

    # 決策日：phase=preview 用最新收盤試算；active 用 11/15 前最後一個交易日定案
    any_df = next(iter(prices.values()))
    tdays = any_df.index
    if phase == "active":
        cutoff = pd.Timestamp(f"{year}-{ENTRY_MMDD}")
        decision = tdays[tdays < cutoff][-1]
        entry_days = tdays[tdays >= cutoff]
        entry_day = entry_days[0] if len(entry_days) else None
    else:
        decision, entry_day = tdays[-1], None

    stocks_out = []
    for g, members in GROUPS.items():
        scored = []
        for c in members:
            df = prices.get(c)
            if df is None:
                continue
            sl = df["Close"].loc[f"{year}-01-01":decision]
            if len(sl) < 60:
                continue
            scored.append((c, float(sl.iloc[-1] / sl.iloc[0] - 1)))
        scored.sort(key=lambda x: -x[1])
        for c, ytd in scored[:TOP_PER_GROUP]:
            df = prices[c]
            cl = df["Close"].loc[:decision]
            ma60 = float(cl.rolling(60).mean().iloc[-1])
            if float(cl.iloc[-1]) <= ma60:   # 趨勢濾網
                continue
            close_now = float(df["Close"].iloc[-1])
            rec = {
                "stock_id": c, "stock_name": GROUPS[g][c], "group": g,
                "ytd_pct": round(ytd * 100, 1), "close": round(close_now, 2),
            }
            if entry_day is not None and entry_day in df.index:
                entry = float(df.loc[entry_day, "Open"])
                rec["entry_ref"] = round(entry, 2)
                rec["stop_line"] = round(entry * (1 - STOP_PCT), 2)
                rec["pnl_pct"] = round((close_now / entry - 1) * 100, 1)
                rec["stop_hit"] = bool(close_now < entry * (1 - STOP_PCT))
            else:
                rec["entry_ref"] = None       # 尚未到進場日
                rec["stop_line"] = round(close_now * (1 - STOP_PCT), 2)
                rec["pnl_pct"] = None
                rec["stop_hit"] = False
            stocks_out.append(rec)

    stocks_out.sort(key=lambda r: -r["ytd_pct"])
    payload["stocks"] = stocks_out
    payload["decision_date"] = str(decision.date())
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"phase={phase} 名單 {len(stocks_out)} 檔 → {OUT_JSON}")

    if stocks_out:
        csv = REPO / "data" / "results" / f"screen_group_result_{ASOF.replace('-', '')}.csv"
        pd.DataFrame(stocks_out).to_csv(csv, index=False, encoding="utf-8-sig")
        print(f"已存檔：{csv}")


if __name__ == "__main__":
    main()
