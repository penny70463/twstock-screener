# -*- coding: utf-8 -*-
"""每日 Line 訊息統一發送（排程最後一步）

run_daily.sh 流程：run_pipeline.py --no-line → ETF 警報/體檢 → 各選股腳本
→ 本腳本讀取所有結果 JSON，組成「一則」合併訊息發送。

新增選股腳本時，讓腳本輸出 JSON 到 data/results/，再於下方
SCREEN_SECTIONS 登記一筆（檔名、標題、格式函式）即可加入每日訊息，
不會多發一則。

用法:
    python send_daily_line.py            # 正式發送
    python send_daily_line.py --dry-run  # 只印出訊息內容，不發送
"""
import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from src.pipeline import (compose_daily_blocks, send_line_message,
                          LINE_LINK_BLOCK)

RESULT_DIR = Path(__file__).resolve().parent / "data" / "results"


def _load(name: str) -> dict | None:
    path = RESULT_DIR / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _format_pullback(data: dict) -> str:
    """回檔轉強段落：檔數 + 回檔最深前 3 檔"""
    stocks = data.get("screened", [])
    if not stocks:
        return "📉 回檔轉強：今日無符合"
    top = "、".join(f"{s['stock_id']} {s['stock_name']}" for s in stocks[:3])
    return f"📉 回檔轉強（台股 {len(stocks)} 檔）\n🎯 回檔最深：{top}"


# 各選股腳本的訊息段落登記表：(結果檔名, 格式函式)
# 新選股腳本 → 輸出 JSON（含 date 與 screened 欄位）→ 在此加一筆
SCREEN_SECTIONS = [
    ("pullback_tw.json", _format_pullback),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="每日 Line 訊息統一發送")
    parser.add_argument("--dry-run", action="store_true",
                        help="只印出訊息內容，不實際發送")
    args = parser.parse_args()

    payloads = {}
    for market in ("TW", "US"):
        data = _load(f"latest_{market.lower()}.json")
        if data:
            payloads[market] = data
    if not payloads:
        print("[-] 找不到每日篩選結果，不發送", flush=True)
        return 1

    blocks = compose_daily_blocks(payloads)

    # 各選股腳本段落：僅納入「與主篩選同日」的結果，過期（腳本失敗）就跳過
    on_date = next(iter(payloads.values()))["date"]
    for filename, formatter in SCREEN_SECTIONS:
        data = _load(filename)
        if data is None:
            continue
        if data.get("date") != on_date:
            print(f"  ! {filename} 日期 {data.get('date')} 與主篩選 {on_date} 不符，略過", flush=True)
            continue
        blocks.append(formatter(data))

    message = "\n\n".join(blocks + [LINE_LINK_BLOCK])

    if args.dry_run:
        print("=== dry-run：以下為將發送的訊息內容 ===")
        print(message)
        return 0

    send_line_message(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
