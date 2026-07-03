# -*- coding: utf-8 -*-
"""族群出量突破偵測：找出近期「同題材多檔接連帶量突破」的族群（雁行擴散）

訊號定義（2026-07 以 3~7 月七波題材行情回測驗證）：
- 出量突破事件：收盤創 60 日新高 + 成交量 >= 20 日均量 2 倍 + 當日漲 >= 3.5%
- 族群行情特徵：同題材多檔在數日窗口內接連觸發（領頭羊先動、族群跟進），
  觸發當日量能 2~10 倍、突破前有 1~3 個月整理平台

流程：
1. 掃描股票池近 10 個交易日的所有出量突破事件
2. 觸發過的股票丟給 LLM 題材分類（重用 src/classifier.py，不用產業別）
3. 依「今日點火檔數、累計檔數」排序輸出族群清單

資料來源：
- 股價：yfinance 6 個月日線；台股最新一日 Yahoo 常缺漏，
  用交易所官方行情（src/advisor/data.fetch_day_quotes）補最後一根 K 線
- 股票池：data/results/universe_tw.json

用法：
    python screen_breakout.py                      # 今天（需為交易日）
    python screen_breakout.py 2026-07-03           # 指定基準日
    python screen_breakout.py 2026-07-03 --no-llm  # 跳過 LLM（無 API key 時）

輸出：終端摘要 + data/results/screen_breakout_result_YYYYMMDD.csv
     + data/results/cluster_tw.json（前端「族群突破」頁簽 + Line 訊息資料來源）
（run_daily.sh 每日排程會執行並隨其他結果一併 commit 同步）
"""
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

ARGS = [a for a in sys.argv[1:] if not a.startswith("-")]
NO_LLM = "--no-llm" in sys.argv
ASOF = ARGS[0] if ARGS else dt.date.today().isoformat()
OUT_CSV = (REPO / "data" / "results"
           / f"screen_breakout_result_{ASOF.replace('-', '')}.csv")
OUT_JSON = REPO / "data" / "results" / "cluster_tw.json"

WINDOW = 10          # 事件觀察窗口（交易日）
BREAK_DAYS = 60      # 突破 N 日收盤新高
VOL_MULT = 2.0       # 量能門檻：20 日均量的倍數
MIN_CHG = 0.035      # 事件日最低漲幅


def main() -> None:
    from src.advisor import data as adv_data  # 延後 import：載入 config/.env

    with open(REPO / "data" / "results" / "universe_tw.json", encoding="utf-8") as f:
        uni = {s["stock_id"]: s for s in json.load(f)["stocks"]}
    suffix = {"上市": ".TW", "上櫃": ".TWO"}
    tickers = {c: c + suffix.get(uni[c].get("市場", "上市"), ".TW") for c in uni}

    print(f"基準日 {ASOF}，下載 {len(tickers)} 檔 6 個月日線 ...", flush=True)
    raw = yf.download(list(tickers.values()), period="6mo", group_by="ticker",
                      auto_adjust=True, progress=False, threads=True)

    # Yahoo 台股日線常缺最新一日 → 用官方行情補
    asof_date = dt.date.fromisoformat(ASOF)
    try:
        quotes = adv_data.fetch_day_quotes(asof_date)
        meta = quotes.set_index("code") if quotes is not None and len(quotes) else None
    except Exception as e:
        print(f"  ! 官方行情抓取失敗：{e}", flush=True)
        meta = None

    events_by_stock: dict[str, list] = {}
    today_info: dict[str, dict] = {}
    has_asof = 0
    for code, tk in tickers.items():
        try:
            df = raw[tk].dropna(subset=["Close"]).loc[:ASOF]
        except KeyError:
            continue
        if len(df) < 80:
            continue
        if (df.index[-1].strftime("%Y-%m-%d") < ASOF and meta is not None
                and code in meta.index):
            row = meta.loc[code]
            if pd.notna(row["close"]) and row["date"] == asof_date:
                df.loc[pd.Timestamp(ASOF)] = {
                    "Open": row["open"], "High": row["high"],
                    "Low": row["low"], "Close": row["close"],
                    "Volume": row["volume"],
                }
        if df.index[-1].strftime("%Y-%m-%d") != ASOF:
            continue
        has_asof += 1

        c, v = df["Close"], df["Volume"]
        hh = c.shift(1).rolling(BREAK_DAYS).max()
        vol20 = v.shift(1).rolling(20).mean()
        chg = c.pct_change()
        mask = (c > hh) & (v >= VOL_MULT * vol20) & (chg >= MIN_CHG)
        win = df.index[-WINDOW:]
        fired = [d for d in win if bool(mask.loc[d])]
        if not fired:
            continue
        last = fired[-1]
        events_by_stock[code] = [d.strftime("%m-%d") for d in fired]
        today_info[code] = {
            "close": round(float(c.iloc[-1]), 2),
            "chg_pct": round(float(chg.iloc[-1]) * 100, 1),
            "fired_today": last.strftime("%Y-%m-%d") == ASOF,
            "vol_x": round(float(v.loc[last] / vol20.loc[last]), 1),
        }

    if has_asof == 0:
        print("無任何個股有基準日 K 線（非交易日？），不更新結果")
        return
    print(f"近 {WINDOW} 日出量突破：{len(events_by_stock)} 檔"
          f"（今日點火 {sum(1 for i in today_info.values() if i['fired_today'])} 檔）",
          flush=True)

    # LLM 題材分類（不用產業別；產業僅作為分類提示）
    themes = []
    codes = list(events_by_stock)
    if codes and not NO_LLM:
        from src.classifier import classify_themes
        stocks_in = [{"code": c, "name": uni[c]["stock_name"],
                      "industry": uni[c].get("industry_category", "")} for c in codes]
        print("LLM 題材分類中 ...", flush=True)
        try:
            themes = classify_themes(stocks_in, market="TW").get("themes", [])
        except Exception as e:
            print(f"  ! LLM 分類失敗：{e}", flush=True)
    if not themes and codes:
        themes = [{"name": "未分類", "reason": "LLM 未執行或失敗",
                   "stocks": [{"code": c, "name": uni[c]["stock_name"]} for c in codes]}]

    # 組族群輸出：每檔附觸發日與今日狀態，族群依（今日點火、累計）排序
    out_themes = []
    for t in themes:
        members = []
        for s in t["stocks"]:
            code = s["code"]
            if code not in events_by_stock:
                continue
            info = today_info[code]
            members.append({
                "stock_id": code, "stock_name": s["name"],
                "industry_category": uni[code].get("industry_category", ""),
                "close": info["close"], "chg_pct": info["chg_pct"],
                "fired_dates": events_by_stock[code],
                "fired_today": info["fired_today"], "vol_x": info["vol_x"],
            })
        if not members:
            continue
        members.sort(key=lambda m: (m["fired_today"], m["chg_pct"]), reverse=True)
        out_themes.append({
            "name": t["name"], "reason": t.get("reason", ""),
            "stocks": members, "count": len(members),
            "fired_today_count": sum(1 for m in members if m["fired_today"]),
        })
    # 「其他」類固定排最後，其餘依（今日點火、累計）降冪
    out_themes.sort(key=lambda t: (t["name"] not in ("其他", "未分類"),
                                   t["fired_today_count"], t["count"]), reverse=True)

    payload = {
        "date": ASOF,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "params": {"window_days": WINDOW, "break_days": BREAK_DAYS,
                   "vol_mult": VOL_MULT, "min_chg": MIN_CHG},
        "themes": out_themes,
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"前端資料已更新：{OUT_JSON}（{len(out_themes)} 個族群）")

    rows = []
    for t in out_themes:
        for m in t["stocks"]:
            rows.append({
                "族群": t["name"], "代號": m["stock_id"], "名稱": m["stock_name"],
                "產業": m["industry_category"], "收盤": m["close"],
                "當日漲%": m["chg_pct"], "今日點火": "是" if m["fired_today"] else "",
                "量倍數": m["vol_x"], "觸發日": " ".join(m["fired_dates"]),
            })
    pd.DataFrame(rows).to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print(f"已存檔：{OUT_CSV}")

    for t in out_themes[:8]:
        names = "、".join(f"{m['stock_name']}{'*' if m['fired_today'] else ''}"
                          for m in t["stocks"][:6])
        print(f"  {t['name']}：今日 {t['fired_today_count']} / 共 {t['count']} 檔｜{names}")


if __name__ == "__main__":
    main()
