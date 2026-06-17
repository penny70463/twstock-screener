"""串接流程：TWSE 排行 → top-N → FinMind 抓歷史算四均線 → LLM 題材分類 → 存結果。"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

import pandas as pd

from config import RESULT_DIR, settings
from src import data_source, ma_filter, ranking
from src.classifier import classify_themes

_FETCH_WORKERS = 8  # 併發數；FinMind 600/hr 額度下抓 200 檔很安全


def _fetch_histories(
    ids: list[str], start: date, on_date: date, verbose: bool
) -> dict[str, pd.DataFrame]:
    histories: dict[str, pd.DataFrame] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as pool:
        futures = {
            pool.submit(data_source.get_stock_history, sid, start, on_date): sid
            for sid in ids
        }
        for fut in as_completed(futures):
            sid = futures[fut]
            try:
                histories[sid] = fut.result()
            except Exception as e:  # 單股失敗不應中斷整批
                if verbose:
                    print(f"  ! {sid} 歷史抓取失敗: {e}")
            done += 1
            if verbose and done % 50 == 0:
                print(f"  歷史抓取進度 {done}/{len(ids)}")
    return histories


def run(classify: bool = True, verbose: bool = True) -> dict:
    # 1. TWSE 全上市當日行情 → 漲幅排行前 N
    today = data_source.get_market_today()
    if today.empty:
        raise RuntimeError("取不到 TWSE 當日行情（可能非交易日或來源異常）")
    on_date: date = today["date"].max()
    gainers = ranking.top_gainers(today, settings.top_n)
    if verbose:
        print(f"[{on_date}] TWSE 共 {len(today)} 檔，取漲幅前 {len(gainers)} 名")

    # 2. 對 top-N 平行抓歷史，算四均線（FinMind 單股 + 快取）
    #    序列逐檔在 CI（跨區網路）會慢到數十分鐘，改用 thread pool 併發。
    start = on_date - timedelta(days=int(settings.max_ma * 2 + 60))
    ids = gainers["stock_id"].tolist()
    histories = _fetch_histories(ids, start, on_date, verbose)

    ma = ma_filter.screen(histories, settings.ma_windows)
    passed_ids = ma[ma["above_all"]]["stock_id"].tolist() if not ma.empty else []
    if verbose:
        print(f"  站上 {settings.ma_windows} 全部均線：{len(passed_ids)} 檔")

    # 3. 合併漲幅 + 均線 + 產業別
    info = data_source.get_stock_info()[["stock_id", "industry_category"]]
    ma_cols = ma.drop(columns=["close"], errors="ignore")  # close 已在 gainers，避免衝突
    result = (
        gainers[gainers["stock_id"].isin(passed_ids)]
        .merge(ma_cols, on="stock_id", how="left")
        .merge(info, on="stock_id", how="left")
        .drop(columns=["above_all"], errors="ignore")
        .sort_values("change_pct", ascending=False)
        .reset_index(drop=True)
    )

    # 4. LLM 題材分類
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
        try:
            themes = classify_themes(stocks)
        except Exception as e:  # 分類失敗不應讓整批排程 crash，仍輸出篩選清單
            print(f"  ! LLM 題材分類失敗，僅輸出篩選清單: {e}")

    payload = {
        "date": on_date.isoformat(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "params": {"top_n": settings.top_n, "ma_windows": settings.ma_windows},
        "universe": "TWSE 上市",
        "screened": json.loads(result.to_json(orient="records", force_ascii=False)),
        "themes": themes.get("themes", []),
    }
    _save(payload, on_date)
    return payload


def _save(payload: dict, on_date: date) -> None:
    for name in (on_date.isoformat(), "latest"):
        (RESULT_DIR / f"{name}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
