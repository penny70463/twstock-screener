# -*- coding: utf-8 -*-
"""產出前端「區間漲跌幅排行」所需的歷史收盤價 JSON。

讀取每日管線已下載的 yfinance 快取（src/advisor/cache/hist_*.pkl），
取 universe_tw.json 中的股票，提取收盤價後輸出至
data/results/price_history_tw.json。

此腳本由 run_daily.sh 自動執行，使用者不需手動觸發。
前端載入此 JSON 後可在 client-side 即時計算任意區間的漲跌幅排行。

用法：
    python build_price_history.py          # 正常執行
    python build_price_history.py --force  # 強制重新產出（忽略快取時效）

輸出：data/results/price_history_tw.json（~2 MB raw, ~600 KB gzip）
"""
import datetime as dt
import json
import pickle
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
CACHE_DIR = REPO / "src" / "advisor" / "cache"
RESULT_DIR = REPO / "data" / "results"
OUT_PATH = RESULT_DIR / "price_history_tw.json"


def _find_latest_cache() -> Path | None:
    """找到最新的歷史股價 pkl 快取。

    快取命名格式：hist_{period}_{YYYYMMDD}.pkl
    優先取 2y，其次取任何可用的 period。
    """
    # 優先找 2y
    candidates = sorted(CACHE_DIR.glob("hist_2y_*.pkl"))
    if not candidates:
        # 退回任意 period
        candidates = sorted(CACHE_DIR.glob("hist_*_*.pkl"))
    return candidates[-1] if candidates else None


def main() -> None:
    # 1. 找到快取檔
    cache_path = _find_latest_cache()
    if cache_path is None:
        print("找不到歷史股價快取（src/advisor/cache/hist_*.pkl），"
              "請先執行 python run_pipeline.py 產生快取。")
        sys.exit(1)
    print(f"讀取快取：{cache_path.name}", flush=True)

    # 2. 載入快取與 universe
    history: dict = pickle.loads(cache_path.read_bytes())
    uni_path = RESULT_DIR / "universe_tw.json"
    if not uni_path.exists():
        print(f"找不到 {uni_path}，請先執行 run_pipeline.py")
        sys.exit(1)
    with open(uni_path, encoding="utf-8") as f:
        uni_stocks = {s["stock_id"]: s for s in json.load(f)["stocks"]}

    # 3. 收集所有交易日（取聯集再排序）
    all_dates: set[str] = set()
    for code in uni_stocks:
        df = history.get(code)
        if df is not None and len(df) > 0:
            for ts in df.index:
                all_dates.add(ts.strftime("%Y-%m-%d"))
    dates = sorted(all_dates)
    date_to_idx = {d: i for i, d in enumerate(dates)}
    print(f"交易日範圍：{dates[0]} ~ {dates[-1]}（共 {len(dates)} 天）", flush=True)

    # 4. 建構每檔股票的收盤價陣列
    stocks_out: dict = {}
    included = 0
    for code, meta in uni_stocks.items():
        df = history.get(code)
        if df is None or len(df) == 0:
            continue
        # 建立 null-filled 陣列，只填有資料的日期
        closes = [None] * len(dates)
        for ts, row in df.iterrows():
            d = ts.strftime("%Y-%m-%d")
            idx = date_to_idx.get(d)
            if idx is not None:
                close_val = row.get("Close")
                if close_val is not None:
                    import math
                    if not math.isnan(close_val):
                        closes[idx] = round(float(close_val), 2)
        stocks_out[code] = {
            "name": meta.get("stock_name", ""),
            "industry": meta.get("industry_category", ""),
            "market": meta.get("市場", "上市"),
            "closes": closes,
        }
        included += 1

    # 5. 輸出 JSON
    payload = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "cache_source": cache_path.name,
        "date_range": [dates[0], dates[-1]],
        "dates": dates,
        "stocks": stocks_out,
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    json_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    OUT_PATH.write_text(json_text, encoding="utf-8")
    size_kb = len(json_text.encode("utf-8")) / 1024
    print(f"已產出：{OUT_PATH}（{included} 檔, {size_kb:.0f} KB）", flush=True)


if __name__ == "__main__":
    main()
