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


def _format_breakout(data: dict) -> str:
    """族群出量突破段落：今日有點火（>=2 檔）的族群，至多 3 個"""
    themes = [t for t in data.get("themes", [])
              if t.get("fired_today_count", 0) >= 2
              and t.get("name") not in ("未分類", "其他")]
    if not themes:
        return "🔥 族群突破：今日無族群點火"
    lines = ["🔥 族群出量突破（近10日）"]
    for t in themes[:3]:
        fired = [m["stock_name"] for m in t["stocks"] if m.get("fired_today")]
        names = "、".join(fired[:6]) + ("…" if len(fired) > 6 else "")
        lines.append(f"・{t['name']}：今日 {t['fired_today_count']} 檔"
                     f"（累計 {t['count']}）{names}")
    return "\n".join(lines)


def _format_group(data: dict) -> str | None:
    """集團作帳段落：僅季節內（11~12月）且有名單時出現，其餘回 None 整段隱藏"""
    if data.get("phase") not in ("preview", "active") or not data.get("stocks"):
        return None
    stocks = data["stocks"]
    held = [s for s in stocks if not s.get("stop_hit")]
    stopped = sum(1 for s in stocks if s.get("stop_hit"))
    names = "、".join(f"{s['stock_id']} {s['stock_name']}" for s in held[:6])
    tag = "預備名單" if data["phase"] == "preview" else "持有中"
    line = f"🏢 集團作帳（{tag} {len(held)} 檔）{names}"
    if stopped:
        line += f"\n🛑 已破線出場 {stopped} 檔"
    return line


def _format_quarter(data: dict) -> str | None:
    """季底法人段落：僅季底窗口（3/6/9/12月最後20日）且有名單時出現"""
    if data.get("phase") not in ("preview", "active") or not data.get("stocks"):
        return None
    stocks = data["stocks"]
    held = [s for s in stocks if not s.get("stop_hit")]
    stopped = sum(1 for s in stocks if s.get("stop_hit"))
    names = "、".join(f"{s['stock_id']} {s['stock_name']}" for s in held[:5])
    tag = "預備名單" if data["phase"] == "preview" else "監控中"
    line = f"📆 季底法人（投信 {tag} {len(held)} 檔）{names}"
    if stopped:
        line += f"\n🛑 已破線出場 {stopped} 檔"
    return line


def _format_event_driven(data: dict) -> str | None:
    """事件驅動段落：展覽會 + 法說會前後供應鏈股"""
    events = data.get("events", [])
    if not events:
        return None
    lines = ["🎪 展覽會供應鏈"]
    for evt in events[:2]:  # 最多顯示 2 個展覽會
        count = evt.get("count", 0)
        stocks = evt.get("stocks", [])
        top_names = "、".join(f"{s['stock_id']}" for s in stocks[:3])
        lines.append(f"・{evt['name']}：{count} 檔符合（{top_names}…）")
    return "\n".join(lines)


# 各選股腳本的訊息段落登記表：(結果檔名, 格式函式)
# 新選股腳本 → 輸出 JSON → 在此加一筆；格式函式回 None 表示該段落本日不顯示
SCREEN_SECTIONS = [
    ("pullback_tw.json", _format_pullback),
    ("cluster_tw.json", _format_breakout),
    ("group_tw.json", _format_group),
    ("quarter_tw.json", _format_quarter),
    ("event_driven_tw.json", _format_event_driven),
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
        section = formatter(data)
        if section:
            blocks.append(section)

    message = "\n\n".join(blocks + [LINE_LINK_BLOCK])

    if args.dry_run:
        print("=== dry-run：以下為將發送的訊息內容 ===")
        print(message)
        return 0

    send_line_message(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
