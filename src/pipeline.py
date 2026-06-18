"""串接流程：Advisor 篩選 → 當日漲幅排序 → LLM 題材分類 → 存結果。"""
from __future__ import annotations

import json
import time
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
