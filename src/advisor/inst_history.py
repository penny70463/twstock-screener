#!/usr/bin/env python3
"""三大法人買賣超歷史庫：回填 + 日常累積，讓籌碼維度可回測

儲存格式 cache/inst_history.pkl：
    {datestr("YYYYMMDD"): {code: {"foreign": 股數, "trust": 股數}}}
假日存空 dict（標記已查過，避免重複請求）。

用法：
    python3 inst_history.py --days 650      # 回填最近 650 個日曆日（可中斷續跑）
    python3 inst_history.py --status        # 檢視目前涵蓋範圍

日常累積：main.py / advisor.py 抓法人資料時會自動寫入本庫，不必手動維護。
"""

import argparse
import datetime as dt
import pickle
import time
from pathlib import Path

import pandas as pd

STORE_PATH = Path(__file__).parent / "cache" / "inst_history.pkl"


def load_store() -> dict:
    if STORE_PATH.exists():
        try:
            return pickle.loads(STORE_PATH.read_bytes())
        except Exception:
            return {}
    return {}


def save_store(store: dict):
    STORE_PATH.parent.mkdir(exist_ok=True)
    STORE_PATH.write_bytes(pickle.dumps(store))


def backfill(calendar_days: int, sleep_s: float = 1.5):
    """往回補抓 N 個日曆日的法人資料，已有的日期跳過（可中斷續跑）"""
    import data  # 延遲匯入避免循環相依

    store = load_store()
    today = dt.date.today()
    targets = [today - dt.timedelta(days=i) for i in range(calendar_days)]
    todo = [d for d in targets if d.strftime("%Y%m%d") not in store]
    print(f"目標 {calendar_days} 個日曆日，已有 {len(targets) - len(todo)}，"
          f"待抓 {len(todo)}（預估 {len(todo) * (sleep_s + 1) / 60:.0f} 分鐘）")

    done = 0
    for day in todo:
        datestr = day.strftime("%Y%m%d")
        try:
            recs: dict = data.t86_one_day(datestr)
            if recs:  # 交易日才抓上櫃
                recs.update(data.tpex_inst_one_day(day))
        except Exception as e:
            # 限流/失敗絕不存庫（避免把失敗誤標成假日），退避後續抓
            print(f"  {datestr} 失敗（{e}），退避 15 秒")
            save_store(store)
            time.sleep(15)
            continue
        store[datestr] = recs
        done += 1
        if recs:
            print(f"  {datestr} ✓ {len(recs)} 檔（進度 {done}/{len(todo)}）")
        if done % 20 == 0:
            save_store(store)  # 每 20 天存一次，中斷不白費
        time.sleep(sleep_s)

    save_store(store)
    trading_days = sum(1 for v in store.values() if v)
    print(f"完成。歷史庫現有 {trading_days} 個交易日。")


def panels() -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """轉成 (foreign, trust) 兩個 date×code 面板（股數，缺值=當日無申報=0）"""
    store = load_store()
    trading = {d: v for d, v in store.items() if v}
    if len(trading) < 10:
        return None
    foreign_rows, trust_rows = {}, {}
    for datestr, recs in trading.items():
        date = pd.Timestamp(datestr)
        foreign_rows[date] = {c: v["foreign"] for c, v in recs.items()}
        trust_rows[date] = {c: v["trust"] for c, v in recs.items()}
    foreign = pd.DataFrame.from_dict(foreign_rows, orient="index").sort_index()
    trust = pd.DataFrame.from_dict(trust_rows, orient="index").sort_index()
    return foreign.fillna(0.0), trust.fillna(0.0)


def status():
    store = load_store()
    trading = sorted(d for d, v in store.items() if v)
    if not trading:
        print("歷史庫是空的。用 `python3 inst_history.py --days 650` 回填。")
        return
    print(f"涵蓋 {trading[0]} ~ {trading[-1]}，共 {len(trading)} 個交易日"
          f"（含假日標記 {len(store)} 筆）")


def main():
    parser = argparse.ArgumentParser(description="法人歷史資料庫")
    parser.add_argument("--days", type=int, help="回填最近 N 個日曆日")
    parser.add_argument("--status", action="store_true", help="檢視涵蓋範圍")
    args = parser.parse_args()
    if args.days:
        backfill(args.days)
    else:
        status()


if __name__ == "__main__":
    main()
