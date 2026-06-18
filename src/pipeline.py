"""串接流程：Advisor 篩選 → 當日漲幅排序 → LLM 題材分類 → 存結果。"""
from __future__ import annotations

import json
import time
import os
import requests
from datetime import date, datetime
from zoneinfo import ZoneInfo

TW_TZ = ZoneInfo("Asia/Taipei")

import pandas as pd

from config import RESULT_DIR, settings
from src.classifier import classify_themes
from src.advisor import data as adv_data
from src.advisor import market as adv_market
from src.advisor import screener as adv_screener

def _step(label: str, t0: float, verbose: bool) -> float:
    now = time.time()
    if verbose:
        print(f"  [+{now - t0:6.1f}s] {label}", flush=True)
    return now

def run(classify: bool = True, verbose: bool = True) -> dict:
    t0 = time.time()
    
    # 1. 取得全市場股票池與歷史資料
    if verbose: print("  取得全市場資料中...", flush=True)
    universe = adv_data.get_universe() # 預設取前 600 大流動性
    tickers = {row.code: row.yahoo for row in universe.itertuples()}
    
    history = adv_data.fetch_history(tickers)
    adv_data.patch_latest_bar(history, universe)
    _step("歷史股價與快取完成", t0, verbose)
    
    # 2. 取得法人與營收資料
    inst = adv_data.fetch_institutional(days=3, lookback=10)
    revenue = adv_data.fetch_revenue()
    _step("三大法人與月營收完成", t0, verbose)
    
    # 3. 取得大盤狀態與門檻
    market_state = adv_market.get_regime()
    threshold = market_state["threshold"]
    if verbose:
        print(f"[{datetime.now(TW_TZ).date()}] 大盤狀態: {market_state['label']}, 建議門檻: {threshold}", flush=True)
    
    # 4. 執行 Advisor 多因子選股
    screened_df, universe_df = adv_screener.run_screen(universe, history, inst, revenue, threshold)
    _step("Advisor 評分與篩選完成", t0, verbose)
    
    if screened_df.empty:
        if verbose: print("  ! 今日無股票通過 Advisor 篩選門檻", flush=True)
        return _build_payload(datetime.now(TW_TZ).date(), pd.DataFrame(), {}, market_state)
        
    # 5. 計算當日漲幅，作為交集排序依據 (長線保護短線，短線找動能)
    change_pcts = []
    for code in screened_df["代號"]:
        df = history.get(code)
        if df is not None and len(df) >= 2:
            pct = (df["Close"].iloc[-1] / df["Close"].iloc[-2] - 1) * 100
            change_pcts.append(round(pct, 2))
        else:
            change_pcts.append(0.0)
            
    screened_df["change_pct"] = change_pcts
    
    # 過濾出今天上漲的，並依照漲幅由高到低排序，只取前 top_n 送給 LLM
    result = screened_df[screened_df["change_pct"] > 0].sort_values("change_pct", ascending=False).head(settings.top_n)
    
    if verbose:
        print(f"  通過 Advisor 門檻且今日上漲共 {len(result)} 檔（排序取前 {settings.top_n}）", flush=True)
        
    # 為了相容前端顯示，將欄位名稱對齊
    result = result.rename(columns={
        "代號": "stock_id",
        "名稱": "stock_name",
        "產業": "industry_category",
        "收盤價": "close",
    })

    # 6. LLM 題材分類
    themes = {"themes": []}
    if classify and not result.empty:
        stocks = [
            {
                "code": r.stock_id,
                "name": r.stock_name or r.stock_id,
                "industry": getattr(r, "industry_category", "") or "",
            }
            for r in result.itertuples()
        ]
        if verbose:
            print(f"  LLM 分類中（{len(stocks)} 檔）...", flush=True)
        try:
            themes = classify_themes(stocks)
        except Exception as e:
            print(f"  ! LLM 題材分類失敗: {e}", flush=True)
        _step("LLM 分類完成", t0, verbose)

    payload = _build_payload(datetime.now(TW_TZ).date(), result, themes, market_state)
    _save(payload, datetime.now(TW_TZ).date())
    
    # 儲存 Universe 給前端投資組合使用
    universe_payload = _build_universe_payload(datetime.now(TW_TZ).date(), universe_df, market_state)
    (RESULT_DIR / "universe.json").write_text(
        json.dumps(universe_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    
    # 產生可用日期清單 (Time Travel)
    _generate_available_dates()
    
    # 發送 Line 廣播推播 (若有設定 Token)
    _send_line_broadcast(payload, result)
    
    return payload

def _build_universe_payload(on_date: date, universe_df: pd.DataFrame, market_state: dict) -> dict:
    records = []
    if not universe_df.empty:
        # 重命名欄位以符合前端
        u_df = universe_df.rename(columns={
            "代號": "stock_id",
            "名稱": "stock_name",
            "產業": "industry_category",
            "收盤價": "close",
        })
        records = json.loads(u_df.to_json(orient="records", force_ascii=False))
        
    return {
        "date": on_date.isoformat(),
        "generated_at": datetime.now(TW_TZ).isoformat(timespec="seconds"),
        "market_state": market_state,
        "stocks": records
    }

def _build_payload(on_date: date, result: pd.DataFrame, themes: dict, market_state: dict) -> dict:
    records = []
    if not result.empty:
        records = json.loads(result.to_json(orient="records", force_ascii=False))
        
    return {
        "date": on_date.isoformat(),
        "generated_at": datetime.now(TW_TZ).isoformat(timespec="seconds"),
        "params": {"top_n": settings.top_n, "advisor_threshold": market_state["threshold"]},
        "market_state": market_state,
        "universe": "TWSE+TPEX Top 流動性",
        "screened": records,
        "themes": themes.get("themes", []),
    }

def _save(payload: dict, on_date: date) -> None:
    for name in (on_date.isoformat(), "latest"):
        (RESULT_DIR / f"{name}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

def _generate_available_dates() -> None:
    """掃描 results 資料夾，產生可用的歷史日期清單"""
    import re
    date_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")
    dates = []
    if RESULT_DIR.exists():
        for f in RESULT_DIR.iterdir():
            match = date_pattern.match(f.name)
            if match:
                dates.append(match.group(1))
    dates.sort(reverse=True) # 最新日期在前
    (RESULT_DIR / "available_dates.json").write_text(
        json.dumps(dates, ensure_ascii=False), encoding="utf-8"
    )

def _send_line_broadcast(payload: dict, result_df: pd.DataFrame) -> None:
    """發送 Line 廣播推播"""
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    if not token or payload is None:
        return
        
    on_date = payload["date"]
    market_label = payload["market_state"]["label"]
    threshold = payload["market_state"]["threshold"]
    
    themes = payload.get("themes", [])
    theme_names = "、".join([t["theme"] for t in themes[:3]]) if themes else "無明顯題材"
    
    # 挑出最強 3 檔
    top_stocks = []
    if not result_df.empty:
        for r in result_df.head(3).itertuples():
            name = getattr(r, "stock_name", "") or ""
            top_stocks.append(f"{r.stock_id} {name}")
    top_stocks_str = "、".join(top_stocks) if top_stocks else "無"
    
    message = (
        f"【台股動能掃描 {on_date}】\n"
        f"📊 大盤狀態：{market_label} (門檻 {threshold}分)\n"
        f"🔥 熱門題材：{theme_names}\n"
        f"🚀 強勢指標：{top_stocks_str}\n\n"
        f"🔗 點此查看完整排行榜與持股體檢：\n"
        f"https://twstock-screener.vercel.app/"
    )
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
    
    allowed_ids_str = os.getenv("LINE_ALLOWED_USER_IDS", "")
    allowed_ids = [uid.strip() for uid in allowed_ids_str.split(",") if uid.strip()]
    
    if allowed_ids:
        # 使用 Multicast (指定發送)
        data = {
            "to": allowed_ids,
            "messages": [{"type": "text", "text": message}]
        }
        api_url = "https://api.line.me/v2/bot/message/multicast"
        print(f"  [>] Line 使用 Multicast 發送給 {len(allowed_ids)} 個特定使用者...", flush=True)
    else:
        # 使用 Broadcast (群發)
        data = {
            "messages": [{"type": "text", "text": message}]
        }
        api_url = "https://api.line.me/v2/bot/message/broadcast"
        print(f"  [>] Line 使用 Broadcast 發送給所有使用者...", flush=True)

    try:
        res = requests.post(api_url, headers=headers, json=data, timeout=10)
        if res.status_code == 200:
            print("  [+] Line 推播發送成功！", flush=True)
        else:
            print(f"  [-] Line 推播失敗: {res.text}", flush=True)
    except Exception as e:
        print(f"  [-] Line 推播發生例外錯誤: {e}", flush=True)
