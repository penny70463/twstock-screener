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
4. 每檔附「每日出場線」= max(進場參考價x0.85, 波段最高收盤x0.75)，
   收盤跌破出場（3 年 3,625 個波段回測選定的停損停利規則，收盤確認執行）

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

import numpy as np
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
WAVE_GAP = 10        # 相鄰事件間隔 <= N 日視為同一波（與回測一致）

# 每日出場線（2026-07 3年/3625波段回測選定：期望 +20.4%/筆，收盤確認執行）
# 出場線 = max(進場參考價 x (1-停損), 波段最高收盤 x (1-移動停利))，收盤跌破出場
EXIT_STOP = 0.15     # 初始停損：進場參考價（波段首日的隔日開盤）往下 15%
EXIT_TRAIL = 0.25    # 移動停利：波段最高收盤回檔 25%


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
        idxs = np.flatnonzero(mask.to_numpy())
        if len(idxs) == 0:
            continue
        # 只看最新一波：從最後事件往回串連（間隔 <= WAVE_GAP 視為同一波）
        wave = [int(idxs[-1])]
        for i in idxs[::-1][1:]:
            if wave[-1] - int(i) <= WAVE_GAP:
                wave.append(int(i))
            else:
                break
        wave = wave[::-1]
        recent = [i for i in wave if i >= len(df) - WINDOW]
        if not recent:
            continue
        last = df.index[recent[-1]]
        events_by_stock[code] = [df.index[i].strftime("%m-%d") for i in recent]

        # 每日出場線：進場參考 = 波段首日的隔日開盤；波段首日就是基準日時，
        # 尚未進場 → 出場線以基準日收盤 x (1-停損) 起算（隔日進場後自動修正）
        first_i = wave[0]
        close_now = float(c.iloc[-1])
        if first_i + 1 < len(df):
            entry_ref = float(df["Open"].iloc[first_i + 1])
            peak = float(c.iloc[first_i + 1:].max())
            exit_line = max(entry_ref * (1 - EXIT_STOP), peak * (1 - EXIT_TRAIL))
            exit_hit = close_now < exit_line
        else:
            entry_ref = None
            exit_line = close_now * (1 - EXIT_STOP)
            exit_hit = False

        today_info[code] = {
            "close": round(close_now, 2),
            "chg_pct": round(float(chg.iloc[-1]) * 100, 1),
            "fired_today": last.strftime("%Y-%m-%d") == ASOF,
            "vol_x": round(float(v.iloc[recent[-1]] / vol20.iloc[recent[-1]]), 1),
            "wave_start": df.index[first_i].strftime("%Y-%m-%d"),
            "entry_ref": round(entry_ref, 2) if entry_ref else None,
            "exit_line": round(exit_line, 2),
            "exit_hit": bool(exit_hit),
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
                "wave_start": info["wave_start"], "entry_ref": info["entry_ref"],
                "exit_line": info["exit_line"], "exit_hit": info["exit_hit"],
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
                   "vol_mult": VOL_MULT, "min_chg": MIN_CHG,
                   "exit_stop": EXIT_STOP, "exit_trail": EXIT_TRAIL},
        "themes": out_themes,
    }
    json_text = json.dumps(payload, ensure_ascii=False)
    OUT_JSON.write_text(json_text, encoding="utf-8")
    # 歷史日期檔：前端選日期時抓對應的 cluster_tw_YYYYMMDD.json
    dated_json = REPO / "data" / "results" / f"cluster_tw_{ASOF.replace('-', '')}.json"
    dated_json.write_text(json_text, encoding="utf-8")
    print(f"前端資料已更新：{OUT_JSON}（{len(out_themes)} 個族群）")

    rows = []
    for t in out_themes:
        for m in t["stocks"]:
            rows.append({
                "族群": t["name"], "代號": m["stock_id"], "名稱": m["stock_name"],
                "產業": m["industry_category"], "收盤": m["close"],
                "當日漲%": m["chg_pct"], "今日點火": "是" if m["fired_today"] else "",
                "量倍數": m["vol_x"], "觸發日": " ".join(m["fired_dates"]),
                "波段起日": m["wave_start"],
                "進場參考": m["entry_ref"] if m["entry_ref"] else "明日開盤",
                "出場線": m["exit_line"],
                "已破線": "出場" if m["exit_hit"] else "",
            })
    pd.DataFrame(rows).to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print(f"已存檔：{OUT_CSV}")

    for t in out_themes[:8]:
        names = "、".join(f"{m['stock_name']}{'*' if m['fired_today'] else ''}"
                          for m in t["stocks"][:6])
        print(f"  {t['name']}：今日 {t['fired_today_count']} / 共 {t['count']} 檔｜{names}")


if __name__ == "__main__":
    main()
