"""週末自動覆盤：比對當週選股結果與最新收盤價，計算策略績效並推播 LINE。

用法:
    python weekly_review.py              # 完整流程（含 LINE 推播）
    python weekly_review.py --no-line    # 只計算不推播（本機測試用）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
TW_TZ = ZoneInfo("Asia/Taipei")
RESULT_DIR = Path(__file__).parent / "data" / "results"
TOP_N = 30  # 追蹤前 N 檔

# 嘗試載入 .env（本機開發用）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ---------------------------------------------------------------------------
# 核心邏輯
# ---------------------------------------------------------------------------
def _load_latest(market: str) -> dict | None:
    """讀取最近一次的選股結果 JSON。"""
    path = RESULT_DIR / f"latest_{market.lower()}.json"
    if not path.exists():
        print(f"  [!] 找不到 {path}，跳過 {market} 市場")
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_tickers(screened: list[dict], market: str) -> tuple[list[str], dict]:
    """從選股清單建立 yfinance ticker 列表與對照表。"""
    tickers = []
    mapping = {}
    for s in screened[:TOP_N]:
        if market == "TW":
            suffix = ".TW" if s.get("市場") == "上市" else ".TWO"
            ticker = f"{s['stock_id']}{suffix}"
        else:
            ticker = s["stock_id"]
        tickers.append(ticker)
        mapping[ticker] = {
            "name": s.get("stock_name", ""),
            "base_price": s.get("close", 0),
            "score": s.get("總分", 0),
        }
    return tickers, mapping


def _fetch_current_prices(tickers: list[str]) -> dict[str, float]:
    """用 yfinance 一次性抓取所有標的最新收盤價。"""
    if not tickers:
        return {}

    print(f"  [>] 正在抓取 {len(tickers)} 檔標的的最新價格...", flush=True)
    df = yf.download(tickers, period="5d", auto_adjust=True, group_by="ticker", progress=False)

    prices = {}
    for ticker in tickers:
        try:
            if len(tickers) == 1:
                # yfinance 只有一檔時不分 group
                close_series = df["Close"].dropna()
            else:
                close_series = df[ticker]["Close"].dropna()
            if not close_series.empty:
                prices[ticker] = float(close_series.iloc[-1])
        except (KeyError, TypeError):
            pass
    return prices


def review_market(market: str) -> dict | None:
    """對單一市場執行覆盤，回傳績效摘要 dict。"""
    data = _load_latest(market)
    if not data:
        return None

    screened = data.get("screened", [])
    if not screened:
        print(f"  [!] {market} 無選股資料")
        return None

    select_date = data.get("date", "unknown")
    tickers, mapping = _build_tickers(screened, market)
    prices = _fetch_current_prices(tickers)

    results = []
    for ticker in tickers:
        if ticker not in prices:
            continue
        current = prices[ticker]
        base = mapping[ticker]["base_price"]
        if base <= 0:
            continue
        ret = round((current / base - 1) * 100, 2)
        results.append({
            "code": ticker,
            "name": mapping[ticker]["name"],
            "base_price": base,
            "current_price": round(current, 2),
            "return_pct": ret,
        })

    if not results:
        print(f"  [!] {market} 無法取得任何價格資料")
        return None

    res_df = pd.DataFrame(results).sort_values("return_pct", ascending=False)
    avg_ret = round(res_df["return_pct"].mean(), 2)
    win_count = int((res_df["return_pct"] > 0).sum())
    total = len(res_df)
    win_rate = round(win_count / total * 100, 1)

    # 最強 / 最弱
    top3 = res_df.head(3)[["name", "return_pct"]].values.tolist()
    bottom3 = res_df.tail(3)[["name", "return_pct"]].values.tolist()
    # 只保留虧損的
    bottom3 = [x for x in bottom3 if x[1] < 0]

    summary = {
        "market": market,
        "select_date": select_date,
        "review_date": datetime.now(TW_TZ).date().isoformat(),
        "total": total,
        "win_count": win_count,
        "win_rate": win_rate,
        "avg_return": avg_ret,
        "top3": [{"name": n, "ret": r} for n, r in top3],
        "bottom3": [{"name": n, "ret": r} for n, r in bottom3],
        "details": results,
    }

    # 終端機輸出
    market_label = "🇹🇼 台股" if market == "TW" else "🇺🇸 美股"
    print(f"\n  {market_label} 覆盤結果 (選股日: {select_date})")
    print(f"  ✅ 勝率: {win_rate}% ({win_count}/{total})")
    print(f"  📈 平均報酬: {'+' if avg_ret >= 0 else ''}{avg_ret}%")
    print(f"  🏆 最強: {', '.join(f'{n} {r:+.2f}%' for n, r in top3)}")
    if bottom3:
        print(f"  ⚠️ 最弱: {', '.join(f'{n} {r:+.2f}%' for n, r in bottom3)}")

    return summary


# ---------------------------------------------------------------------------
# LINE 推播
# ---------------------------------------------------------------------------
def send_line_review(summaries: list[dict]) -> None:
    """透過 LINE 推播覆盤報告。"""
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    if not token:
        print("  [!] LINE_CHANNEL_ACCESS_TOKEN 未設定，跳過推播")
        return

    review_date = summaries[0]["review_date"] if summaries else datetime.now(TW_TZ).date().isoformat()
    lines = [f"【📊 週末覆盤 {review_date}】"]

    for s in summaries:
        market_label = "🇹🇼 台股" if s["market"] == "TW" else "🇺🇸 美股"
        top_str = "、".join(f"{t['name']} {t['ret']:+.1f}%" for t in s["top3"])
        
        block = (
            f"\n📍 {market_label} (選股日: {s['select_date']})\n"
            f"✅ 勝率: {s['win_rate']}% ({s['win_count']}/{s['total']})\n"
            f"📈 平均報酬: {'+' if s['avg_return'] >= 0 else ''}{s['avg_return']}%\n"
            f"🏆 最強: {top_str}"
        )
        
        if s["bottom3"]:
            bottom_str = "、".join(f"{b['name']} {b['ret']:+.1f}%" for b in s["bottom3"])
            block += f"\n⚠️ 最弱: {bottom_str}"

        lines.append(block)

    lines.append(f"\n🔗 完整排行: https://twstock-screener.vercel.app/")
    message = "\n".join(lines)

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    allowed_ids_str = os.getenv("LINE_ALLOWED_USER_IDS", "")
    allowed_ids = [uid.strip() for uid in allowed_ids_str.split(",") if uid.strip()]

    if allowed_ids:
        data = {"to": allowed_ids, "messages": [{"type": "text", "text": message}]}
        api_url = "https://api.line.me/v2/bot/message/multicast"
        print(f"  [>] LINE 使用 Multicast 發送給 {len(allowed_ids)} 個使用者...", flush=True)
    else:
        data = {"messages": [{"type": "text", "text": message}]}
        api_url = "https://api.line.me/v2/bot/message/broadcast"
        print(f"  [>] LINE 使用 Broadcast 發送給所有使用者...", flush=True)

    try:
        res = requests.post(api_url, headers=headers, json=data, timeout=10)
        if res.status_code == 200:
            print("  [+] LINE 覆盤推播發送成功！", flush=True)
        else:
            print(f"  [-] LINE 推播失敗: {res.text}", flush=True)
    except Exception as e:
        print(f"  [-] LINE 推播發生例外錯誤: {e}", flush=True)


# ---------------------------------------------------------------------------
# 主程式
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="週末自動覆盤：台美股策略績效追蹤")
    parser.add_argument("--no-line", action="store_true", help="跳過 LINE 推播（本機測試用）")
    args = parser.parse_args()

    print(f"\n{'='*40}")
    print(f"📊 週末自動覆盤 - {datetime.now(TW_TZ).strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*40}")

    summaries = []
    for market in ["TW", "US"]:
        summary = review_market(market)
        if summary:
            summaries.append(summary)

    if not summaries:
        print("\n[!] 沒有任何市場資料可供覆盤")
        return 1

    # 儲存覆盤結果 JSON
    review_payload = {
        "review_date": datetime.now(TW_TZ).date().isoformat(),
        "generated_at": datetime.now(TW_TZ).isoformat(timespec="seconds"),
        "markets": summaries,
    }
    review_path = RESULT_DIR / "weekly_review.json"
    review_path.write_text(
        json.dumps(review_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n[+] 覆盤結果已儲存 -> {review_path}")

    # LINE 推播
    if not args.no_line:
        send_line_review(summaries)
    else:
        print("  [i] 已跳過 LINE 推播 (--no-line)")

    print(f"\n{'='*40}")
    print(f"✅ 覆盤完成！")
    print(f"{'='*40}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
