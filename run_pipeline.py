"""排程入口：GitHub Actions / 本地 cron 直接執行此檔。

用法:
    python run_pipeline.py            # 完整流程（含 LLM 分類）
    python run_pipeline.py --no-llm   # 只篩選不分類（省 API）
"""
from __future__ import annotations

import argparse
import sys

from src.pipeline import run


def main() -> int:
    parser = argparse.ArgumentParser(description="台股強勢股篩選 + 題材分類")
    parser.add_argument("--no-llm", action="store_true", help="跳過 LLM 題材分類")
    args = parser.parse_args()

    payload = run(classify=not args.no_llm)
    n = len(payload["screened"])
    t = len(payload["themes"])
    print(f"[{payload['date']}] 通過篩選 {n} 檔，分為 {t} 個題材族群 -> data/results/latest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
