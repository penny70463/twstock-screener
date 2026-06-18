"""排程入口：GitHub Actions / 本地 cron 直接執行此檔。

用法:
    python run_pipeline.py            # 完整流程（含 LLM 分類）
    python run_pipeline.py --no-llm   # 只篩選不分類（省 API）
"""
from __future__ import annotations

import argparse
import sys

from src.pipeline import run, send_combined_line_broadcast


def main() -> int:
    parser = argparse.ArgumentParser(description="台美股強勢股篩選 + 題材分類")
    parser.add_argument("--no-llm", action="store_true", help="跳過 LLM 題材分類")
    parser.add_argument("--market", type=str, choices=["TW", "US", "ALL"], default="ALL", help="指定執行的市場 (TW, US, 或 ALL)")
    args = parser.parse_args()

    markets = ["TW", "US"] if args.market == "ALL" else [args.market]
    
    payloads = {}
    
    for m in markets:
        print(f"\n{'='*40}")
        print(f"🚀 開始執行 {m} 市場篩選流程...")
        print(f"{'='*40}")
        payload = run(market=m, classify=not args.no_llm)
        payloads[m] = payload
        n = len(payload["screened"])
        t = len(payload["themes"])
        print(f"[{payload['date']}] {m} 市場通過篩選 {n} 檔，分為 {t} 個題材族群 -> data/results/latest_{m.lower()}.json")
        
    send_combined_line_broadcast(payloads)
        
    return 0


if __name__ == "__main__":
    sys.exit(main())
