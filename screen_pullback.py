# -*- coding: utf-8 -*-
"""回檔轉強篩選：均線之上 + 連兩月營收年增月增 + 自月高回檔7% + 回測支撐後轉強

四條件（2026-07 與使用者逐項確認的定義）：
1. 收盤價站上季線（60日）、半年線（120日）、年線（240日）
2. 基準日已公布的最近兩個月營收，年增率與月增率皆 > 0
   （每月 10 日前公布上月營收：基準日在 10 日前，最近月 = 兩個月前）
3. 近一個月（21 個交易日）盤中高點之後，曾回檔 ≥ 7%（以盤中低點衡量）
4. 基準日當天完成轉強動作：
   - 回檔期間「收盤」未跌破當日季線（容許 1%；盤中下影線刺破不算破線，
     使用者確認採收盤價判定——洗盤容忍）
   - 修正期間收盤曾跌破 5 日線，基準日收盤重新站上
   - 基準日收盤突破前一交易日盤中高點

資料來源：
- 月營收：公開資訊觀測站彙總表（舊版主機 mopsov.twse.com.tw，上市+上櫃+KY）
- 股價：yfinance 日線（截至基準日）
- 股票池：data/results/universe_tw.json（已含股價≥10元、日成交≥1億過濾）

用法：
    python screen_pullback.py 2026-07-02     # 指定盤後基準日
    python screen_pullback.py               # 預設今天（需為交易日）

相依套件：pandas yfinance requests lxml
輸出：終端表格 + data/results/screen_pullback_result_YYYYMMDD.csv
     + data/results/pullback_tw.json（前端「回檔轉強」頁簽資料來源）
（run_daily.sh 每日排程會執行並隨其他結果一併 commit 同步）
"""
import io
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

REPO = Path(__file__).resolve().parent
ASOF = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
OUT_CSV = (REPO / "data" / "results"
           / f"screen_pullback_result_{ASOF.replace('-', '')}.csv")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

PULLBACK_PCT = 0.07     # 條件3：自近一月高點回檔幅度門檻
MONTH_WIN = 21          # 近一個月 ≈ 21 個交易日
SUPPORT_TOL = 0.99      # 支撐判定容許 1% 誤差
MIN_BARS = 245          # 年線需要 240 根以上


def _latest_two_months(asof: str) -> list[tuple[int, int]]:
    """基準日已公布的最近兩個月（西元年, 月）。每月 10 日為上月營收公布期限。"""
    d = date.fromisoformat(asof)
    y, m = d.year, d.month - 1          # 上個月
    if d.day <= 10:                     # 上月營收可能尚未公布完，再往前一個月
        m -= 1
    if m <= 0:
        m += 12
        y -= 1
    prev_y, prev_m = (y, m - 1) if m > 1 else (y - 1, 12)
    return [(prev_y, prev_m), (y, m)]


def _fetch_page(url: str, retries: int = 3) -> list[pd.DataFrame]:
    """抓單一彙總頁並解析出含「公司代號」的表格；重試後仍失敗則拋例外。

    觀測站對連續請求會暫時拒連或回錯誤頁，需重試 + 拉長間隔。"""
    last_err: Exception | None = None
    for attempt in range(retries):
        if attempt:
            time.sleep(15 * attempt)
        try:
            r = requests.get(url, headers=HEADERS, timeout=60)
            r.encoding = "big5"
            tables = pd.read_html(io.StringIO(r.text), flavor="lxml")
        except Exception as e:
            last_err = e
            continue
        found = []
        for t in tables:
            if isinstance(t.columns, pd.MultiIndex):
                t.columns = [c[-1] for c in t.columns]
            # 欄名內含空白（如「公司 代號」），移除後比對
            t.columns = [str(c).replace(" ", "") for c in t.columns]
            if "公司代號" in t.columns:
                found.append(t)
        if found:
            return found
        last_err = ValueError("頁面無彙總表（可能被觀測站限流）")
    raise RuntimeError(f"{url} 抓取失敗：{last_err}")


def fetch_revenue_month(year: int, month: int) -> pd.DataFrame:
    """公開資訊觀測站月營收彙總表（上市 sii / 上櫃 otc，含 KY）→ yoy/mom

    已公布月份的資料不會再變動，故快取於 src/advisor/cache/；
    四個來源頁（sii/otc × 國內/KY）缺一即中止，避免用殘缺資料出結果。"""
    cache = REPO / "src" / "advisor" / "cache" / f"mops_t21_{year}_{month:02d}.csv"
    if cache.exists():
        df = pd.read_csv(cache, dtype={"code": str}).set_index("code")
        print(f"  {month}月：使用快取（{len(df)} 家）", flush=True)
        return df

    year_roc = year - 1911
    frames = []
    for market in ("sii", "otc"):
        for suffix in ("0", "1"):       # 0=國內, 1=KY
            url = (f"https://mopsov.twse.com.tw/nas/t21/{market}/"
                   f"t21sc03_{year_roc}_{month}_{suffix}.html")
            frames.extend(_fetch_page(url))
            time.sleep(3)
    df = pd.concat(frames, ignore_index=True)
    df = df[df["公司代號"].astype(str).str.match(r"^\d{4}$")].copy()
    df["code"] = df["公司代號"].astype(str)
    df["yoy"] = pd.to_numeric(df["去年同月增減(%)"], errors="coerce")
    df["mom"] = pd.to_numeric(df["上月比較增減(%)"], errors="coerce")
    out = df.drop_duplicates("code").set_index("code")[["yoy", "mom"]]
    cache.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(cache, encoding="utf-8-sig")
    return out


def main() -> None:
    (y1, m1), (y2, m2) = _latest_two_months(ASOF)
    print(f"基準日 {ASOF}，營收採 {y1}/{m1} 與 {y2}/{m2}", flush=True)

    print("步驟 1/3：抓取月營收彙總表 ...", flush=True)
    rev_a, rev_b = fetch_revenue_month(y1, m1), fetch_revenue_month(y2, m2)
    print(f"  {m1}月 {len(rev_a)} 家、{m2}月 {len(rev_b)} 家", flush=True)
    rev = rev_a.join(rev_b, lsuffix="_a", rsuffix="_b", how="inner")
    rev_pass = rev[(rev["yoy_a"] > 0) & (rev["mom_a"] > 0)
                   & (rev["yoy_b"] > 0) & (rev["mom_b"] > 0)]
    print(f"  兩個月皆「年增且月增」：{len(rev_pass)} 家", flush=True)

    with open(REPO / "data" / "results" / "universe_tw.json", encoding="utf-8") as f:
        uni = {s["stock_id"]: s for s in json.load(f)["stocks"]}
    codes = [c for c in rev_pass.index if c in uni]
    print(f"  與股票池（{len(uni)} 檔）交集：{len(codes)} 檔", flush=True)
    if not codes:
        raise SystemExit("營收關後無任何股票，中止（請檢查營收資料是否完整）")

    print("步驟 2/3：下載股價（2 年日線）...", flush=True)
    suffix = {"上市": ".TW", "上櫃": ".TWO"}
    tickers = {c: c + suffix.get(uni[c].get("市場", "上市"), ".TW") for c in codes}
    raw = yf.download(list(tickers.values()), period="2y", group_by="ticker",
                      auto_adjust=True, progress=False, threads=True)

    print("步驟 3/3：套用價格條件 ...", flush=True)
    counts = {"資料足夠": 0, "c1_均線之上": 0, "c3_曾回檔7%": 0, "c4_轉強": 0}
    hits = []
    hits_json = []
    for code, tk in tickers.items():
        try:
            df = raw[tk].dropna(subset=["Close"])
        except KeyError:
            continue
        df = df.loc[:ASOF]
        if len(df) < MIN_BARS or df.index[-1].strftime("%Y-%m-%d") != ASOF:
            continue
        counts["資料足夠"] += 1
        c = df["Close"]
        close = float(c.iloc[-1])

        # 條件1：收盤 > 季線/半年線/年線
        ma5 = c.rolling(5).mean()
        ma60, ma120, ma240 = (float(c.rolling(n).mean().iloc[-1])
                              for n in (60, 120, 240))
        if not (close > ma60 and close > ma120 and close > ma240):
            continue
        counts["c1_均線之上"] += 1

        # 條件3：近一月高點後曾回檔 ≥ 7%
        win = df.iloc[-MONTH_WIN:]
        hi_pos = int(win["High"].values.argmax())
        if hi_pos >= len(win) - 1:      # 高點就是基準日 → 沒有回檔過程
            continue
        high = float(win["High"].iloc[hi_pos])
        pull = win.iloc[hi_pos + 1:]    # 高點次日 ~ 基準日
        depth = 1 - float(pull["Low"].min()) / high
        if depth < PULLBACK_PCT:
            continue
        counts["c3_曾回檔7%"] += 1

        # 條件4：回檔期間收盤守住季線 + 曾破5日線 + 基準日站回並突破前日高
        ma60_s = c.rolling(60).mean()
        pull_ex = pull.iloc[:-1]        # 回檔期間（不含基準日）
        if len(pull_ex) == 0:
            continue
        support_held = bool(
            (pull_ex["Close"] >= ma60_s.loc[pull_ex.index] * SUPPORT_TOL).all())
        was_below_ma5 = bool((pull_ex["Close"] < ma5.loc[pull_ex.index]).any())
        above_ma5 = close > float(ma5.iloc[-1])
        break_prev_high = close > float(df["High"].iloc[-2])
        if not (support_held and was_below_ma5 and above_ma5 and break_prev_high):
            continue
        counts["c4_轉強"] += 1

        r = rev_pass.loc[code]
        hits.append({
            "代號": code, "名稱": uni[code]["stock_name"],
            "市場": uni[code].get("市場", ""),
            "產業": uni[code].get("industry_category", ""),
            "收盤": round(close, 2),
            "距季線%": round((close / ma60 - 1) * 100, 1),
            "距年線%": round((close / ma240 - 1) * 100, 1),
            "月高": round(high, 2), "回檔深度%": round(depth * 100, 1),
            f"{m1}月YoY%": r["yoy_a"], f"{m1}月MoM%": r["mom_a"],
            f"{m2}月YoY%": r["yoy_b"], f"{m2}月MoM%": r["mom_b"],
        })
        hits_json.append({
            "stock_id": code, "stock_name": uni[code]["stock_name"],
            "market": uni[code].get("市場", ""),
            "industry_category": uni[code].get("industry_category", ""),
            "close": round(close, 2),
            "month_high": round(high, 2),
            "pullback_pct": round(depth * 100, 1),
            "dist_ma60_pct": round((close / ma60 - 1) * 100, 1),
            "dist_ma240_pct": round((close / ma240 - 1) * 100, 1),
            "revenue": [
                {"month": f"{y1}-{m1:02d}", "yoy": float(r["yoy_a"]),
                 "mom": float(r["mom_a"])},
                {"month": f"{y2}-{m2:02d}", "yoy": float(r["yoy_b"]),
                 "mom": float(r["mom_b"])},
            ],
            "sparkline": [round(float(x), 2) for x in c.iloc[-20:]],
        })

    print(f"\n漏斗統計: {counts}", flush=True)

    # 前端 JSON：無任何個股有當日資料（非交易日）時不覆寫，保留前一交易日結果
    if counts["資料足夠"] > 0:
        hits_json.sort(key=lambda h: h["pullback_pct"], reverse=True)
        payload = {
            "date": ASOF,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "params": {"pullback_pct": PULLBACK_PCT, "month_window": MONTH_WIN,
                       "revenue_months": [f"{y1}-{m1:02d}", f"{y2}-{m2:02d}"]},
            "screened": hits_json,
        }
        json_path = REPO / "data" / "results" / "pullback_tw.json"
        json_path.write_text(json.dumps(payload, ensure_ascii=False),
                             encoding="utf-8")
        print(f"前端資料已更新：{json_path}（{len(hits_json)} 檔）")

    out = pd.DataFrame(hits)
    if out.empty:
        print("無符合全部四條件的個股（若全數卡在「資料足夠」，請確認基準日為交易日）")
        return
    out = out.sort_values("回檔深度%", ascending=False)
    out.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print(out.to_string(index=False))
    print(f"\n已存檔：{OUT_CSV}")


if __name__ == "__main__":
    main()
