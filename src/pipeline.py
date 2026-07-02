"""串接流程：Advisor 篩選 → 當日漲幅排序 → LLM 題材分類 → 存結果。"""
from __future__ import annotations

import json
import math
import time
import os
import requests
from datetime import date, datetime
from zoneinfo import ZoneInfo

TW_TZ = ZoneInfo("Asia/Taipei")

import pandas as pd

from config import RESULT_DIR, settings
from src.classifier import classify_themes
from src.advisor import config as adv_config
from src.advisor import data as adv_data
from src.advisor import market as adv_market
from src.advisor import screener as adv_screener
from src.advisor import us_screener as adv_us_screener

def _step(label: str, t0: float, verbose: bool) -> float:
    now = time.time()
    if verbose:
        print(f"  [+{now - t0:6.1f}s] {label}", flush=True)
    return now

def _apply_industry_cap(df: pd.DataFrame, ratio: float = adv_config.SCREEN_MAX_INDUSTRY_RATIO) -> pd.DataFrame:
    """單一產業前綴佔比上限：清單任一前段內同產業佔比不得超過 ratio（不剔除）。

    逐位置重建排序：第 p 名時同產業最多 ceil(ratio*p) 檔（如 Top10 內最多 4 檔），
    超額者順延至後方位置，被延後的股票標記 capped=True。
    動能選股天然群聚於強勢產業；此上限保留產業聚焦特性，
    但避免單一產業 beta 主導排行榜頂部（LINE Top 3 / 前端前段）。
    無產業資料者不設限。
    """
    if df.empty or "產業" not in df.columns:
        return df
    remaining: list = []  # (原始名次, index, 產業)
    for rank, (idx, ind) in enumerate(zip(df.index, df["產業"])):
        ind = ind.strip() if isinstance(ind, str) else ""
        remaining.append((rank, idx, ind))
    counts: dict[str, int] = {}
    ordered: list = []   # (index, capped)
    while remaining:
        pos = len(ordered) + 1
        cap = max(1, math.ceil(ratio * pos))
        pick = next(
            (i for i, (_, _, ind) in enumerate(remaining)
             if not ind or counts.get(ind, 0) + 1 <= cap),
            None,
        )
        if pick is None:
            # 剩餘全數超額：依原排序附於尾端並標記
            ordered.extend((idx, True) for _, idx, _ in remaining)
            break
        rank, idx, ind = remaining.pop(pick)
        if ind:
            counts[ind] = counts.get(ind, 0) + 1
        ordered.append((idx, rank < pos - 1))  # 最終名次比原名次差 → 被上限延後
    out = df.loc[[idx for idx, _ in ordered]].copy()
    out["capped"] = [capped for _, capped in ordered]
    return out

def run(market: str = "TW", classify: bool = True, verbose: bool = True) -> dict:
    t0 = time.time()
    
    # 1. 取得全市場股票池與歷史資料
    if verbose: print(f"  取得 {market} 市場資料中...", flush=True)
    universe = adv_data.get_universe(market=market) # 依據市場取股票池
    tickers = {row.code: row.yahoo for row in universe.itertuples()}
    
    history = adv_data.fetch_history(tickers)
    adv_data.patch_latest_bar(history, universe)
    _step("歷史股價與快取完成", t0, verbose)
    
    # 2. 取得法人與營收資料 (僅台股支援)
    if market == "TW":
        inst = adv_data.fetch_institutional(days=3, lookback=10)
        revenue = adv_data.fetch_revenue()
        _step("三大法人與月營收完成", t0, verbose)
    else:
        inst, revenue = None, None
    
    # 3. 取得大盤狀態與門檻
    market_state = adv_market.get_regime(market=market)
    threshold = market_state["threshold"]
    if verbose:
        print(f"[{datetime.now(TW_TZ).date()}] 大盤狀態: {market_state['label']}, 建議門檻: {threshold}", flush=True)
    
    # 4. 執行選股：台股用五因子；美股用橫斷面動能策略（回測驗證的美股原生策略）
    screener = adv_us_screener if market == "US" else adv_screener
    screened_df, universe_df = screener.run_screen(universe, history, inst, revenue, threshold, market=market)
    _step("評分與篩選完成", t0, verbose)
    
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

    if market == "US":
        # 動能策略：按動能總分排序（非當日漲幅；持倉型策略不追單日跳動）
        result = screened_df.sort_values("總分", ascending=False).head(settings.top_n)
        if verbose:
            print(f"  通過動能門檻共 {len(result)} 檔（按總分排序取前 {settings.top_n}）", flush=True)
    else:
        # 台股：長線保護短線，過濾出今天上漲的，依漲幅排序取前 top_n 送給 LLM
        result = screened_df[screened_df["change_pct"] > 0].sort_values("change_pct", ascending=False).head(settings.top_n)
        if verbose:
            print(f"  通過 Advisor 門檻且今日上漲共 {len(result)} 檔（排序取前 {settings.top_n}）", flush=True)

    # 5.5 產業集中度上限：排行榜任一前段內同產業佔比 ≤ 上限，超額者延後名次（不剔除）
    result = _apply_industry_cap(result)
    if verbose and "capped" in result.columns:
        n_capped = int(result["capped"].sum())
        if n_capped:
            print(f"  產業集中度上限 {adv_config.SCREEN_MAX_INDUSTRY_RATIO:.0%}："
                  f"{n_capped} 檔超額延後名次", flush=True)

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
            themes = classify_themes(stocks, market=market)
        except Exception as e:
            print(f"  ! LLM 題材分類失敗: {e}", flush=True)
        _step("LLM 分類完成", t0, verbose)

    # 計算連續水位模型（三因子曝險建議）
    try:
        close_panel = pd.DataFrame({code: df["Close"] for code, df in history.items() if df is not None and "Close" in df.columns})
        if not close_panel.empty:
            exposure_info = adv_market.get_exposure_live(close_panel, market=market)
            market_state["exposure"] = exposure_info["exposure"]
            market_state["trend_score"] = exposure_info["trend"]
            market_state["breadth"] = exposure_info["breadth"]
            market_state["realized_vol"] = exposure_info["realized_vol"]
            market_state["vol_scale"] = exposure_info["vol_scale"]
            if verbose:
                print(f"  連續水位模型: 建議曝險 {exposure_info['exposure']*100:.0f}% "
                      f"(趨勢={exposure_info['trend']}, 寬度={exposure_info['breadth']}%, "
                      f"波動率={exposure_info['realized_vol']}%)", flush=True)
        _step("連續水位模型完成", t0, verbose)
    except Exception as e:
        if verbose:
            print(f"  ! 連續水位模型計算失敗（不影響選股）: {e}", flush=True)

    payload = _build_payload(datetime.now(TW_TZ).date(), result, themes, market_state, market=market)
    _save(payload, datetime.now(TW_TZ).date(), market=market)
    
    # 儲存 Universe 給前端投資組合使用
    universe_payload = _build_universe_payload(datetime.now(TW_TZ).date(), universe_df, market_state, market=market)
    (RESULT_DIR / f"universe_{market.lower()}.json").write_text(
        json.dumps(universe_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    
    # 產生可用日期清單 (Time Travel)
    _generate_available_dates(market=market)
    
    return payload

def _build_universe_payload(on_date: date, universe_df: pd.DataFrame, market_state: dict, market: str = "TW") -> dict:
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

def _build_payload(on_date: date, result: pd.DataFrame, themes: dict, market_state: dict, market: str = "TW") -> dict:
    records = []
    if not result.empty:
        records = json.loads(result.to_json(orient="records", force_ascii=False))
        
    return {
        "date": on_date.isoformat(),
        "generated_at": datetime.now(TW_TZ).isoformat(timespec="seconds"),
        "params": {"top_n": settings.top_n, "advisor_threshold": market_state["threshold"]},
        "market_state": market_state,
        "universe": f"{market} Market",
        "screened": records,
        "themes": themes.get("themes", []),
    }

def _save(payload: dict, on_date: date, market: str = "TW") -> None:
    for name in (f"{on_date.isoformat()}_{market.lower()}", f"latest_{market.lower()}"):
        (RESULT_DIR / f"{name}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

def _generate_available_dates(market: str = "TW") -> None:
    """掃描 results 資料夾，產生可用的歷史日期清單"""
    import re
    date_pattern = re.compile(rf"^(\d{{4}}-\d{{2}}-\d{{2}})_{market.lower()}\.json$")
    dates = []
    if RESULT_DIR.exists():
        for f in RESULT_DIR.iterdir():
            match = date_pattern.match(f.name)
            if match:
                dates.append(match.group(1))
    dates.sort(reverse=True) # 最新日期在前
    (RESULT_DIR / f"available_dates_{market.lower()}.json").write_text(
        json.dumps(dates, ensure_ascii=False), encoding="utf-8"
    )

def send_combined_line_broadcast(payloads: dict[str, dict]) -> None:
    """發送合併的 Line 廣播推播"""
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    if not token or not payloads:
        return
        
    messages = []
    # 取第一個有資料的日期
    on_date = next(iter(payloads.values()))["date"]
    
    messages.append(f"【台美動能掃描 {on_date}】")
    
    for market in ["TW", "US"]:
        if market not in payloads:
            continue
            
        payload = payloads[market]
        market_label = payload["market_state"]["label"]
        threshold = payload["market_state"]["threshold"]
        
        themes = payload.get("themes", [])
        theme_names = "、".join([t.get("name", "") for t in themes[:3]]) if themes else "無明顯題材"
        
        # 挑出最強 3 檔
        screened = payload.get("screened", [])
        top_stocks = []
        for r in screened[:3]:
            name = r.get("stock_name", "") or ""
            top_stocks.append(f"{r.get('stock_id')} {name}".strip())
        top_stocks_str = "、".join(top_stocks) if top_stocks else "無"
        
        market_name = "🇹🇼台股" if market == "TW" else "🇺🇸美股"
        messages.append(
            f"📍 {market_name} ({market_label} {threshold}分)\n"
            f"🔥 題材：{theme_names}\n"
            f"🚀 指標：{top_stocks_str}"
        )
        
    messages.append(f"🔗 點此查看完整排行榜與持股體檢：\nhttps://twstock-screener.vercel.app/")
    
    message = "\n\n".join(messages)
    
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
            print("  [+] 統一 Line 推播發送成功！", flush=True)
        else:
            print(f"  [-] Line 推播失敗: {res.text}", flush=True)
    except Exception as e:
        print(f"  [-] Line 推播發生例外錯誤: {e}", flush=True)
