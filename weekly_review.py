"""週末自動覆盤：比對當週選股結果與最新收盤價，計算策略績效並推播 LINE。

用法:
    python weekly_review.py              # 完整流程（含 LINE 推播）
    python weekly_review.py --no-line    # 只計算不推播（本機測試用）
"""
from __future__ import annotations

import argparse
import json
import os
import re
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
ALERT_WEEKS = 4  # 健康警報回望週數
REVIEW_DIR = RESULT_DIR / "reviews"  # 歷史覆盤存放目錄

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
# 策略健康警報
# ---------------------------------------------------------------------------
def _load_past_reviews(weeks: int = ALERT_WEEKS) -> list[dict]:
    """讀取最近 N 週的歷史覆盤 JSON（按日期由新到舊）。"""
    if not REVIEW_DIR.exists():
        return []
    pattern = re.compile(r"^weekly_review_(\d{4}-\d{2}-\d{2})\.json$")
    files = []
    for f in REVIEW_DIR.iterdir():
        m = pattern.match(f.name)
        if m:
            files.append((m.group(1), f))
    files.sort(key=lambda x: x[0], reverse=True)

    reviews = []
    for _, fpath in files[:weeks]:
        try:
            with open(fpath, "r", encoding="utf-8") as fp:
                reviews.append(json.load(fp))
        except (json.JSONDecodeError, IOError):
            pass
    return reviews


def _check_health(current_summaries: list[dict]) -> list[str]:
    """根據近幾週的覆盤結果，產生策略健康警報訊息。"""
    past_reviews = _load_past_reviews()
    if not past_reviews:
        return []  # 歷史資料不足，不產生警報

    alerts = []
    for market in ["TW", "US"]:
        market_label = "🇹🇼 台股" if market == "TW" else "🇺🇸 美股"

        # 收集本週 + 歷史的勝率與報酬
        win_rates = []
        avg_returns = []

        # 本週
        current = next((s for s in current_summaries if s["market"] == market), None)
        if current:
            win_rates.append(current["win_rate"])
            avg_returns.append(current["avg_return"])

        # 歷史
        for review in past_reviews:
            for ms in review.get("markets", []):
                if ms["market"] == market:
                    win_rates.append(ms["win_rate"])
                    avg_returns.append(ms["avg_return"])

        if len(win_rates) < 2:
            continue  # 至少要有 2 週才能判斷趨勢

        # 取最近 N 週（含本週）
        recent_wr = win_rates[:ALERT_WEEKS]
        recent_ret = avg_returns[:ALERT_WEEKS]

        # 🔴 嚴重警告：連續 4 週勝率 < 50% 或平均報酬為負
        if len(recent_wr) >= 4 and all(wr < 50 for wr in recent_wr[:4]):
            alerts.append(
                f"🔴 {market_label} 連續 4 週勝率低於 50%！"
                f"近 4 週勝率: {', '.join(f'{wr}%' for wr in recent_wr[:4])}。"
                f"建議檢視因子權重與篩選邏輯。"
            )
        elif len(recent_ret) >= 4 and all(r < 0 for r in recent_ret[:4]):
            alerts.append(
                f"🔴 {market_label} 連續 4 週平均報酬為負！"
                f"近 4 週報酬: {', '.join(f'{r:+.1f}%' for r in recent_ret[:4])}。"
                f"建議檢視因子權重與篩選邏輯。"
            )
        # 🟡 留意：連續 2 週勝率 < 50%
        elif len(recent_wr) >= 2 and all(wr < 50 for wr in recent_wr[:2]):
            alerts.append(
                f"🟡 {market_label} 連續 2 週勝率低於 50%，"
                f"近 2 週勝率: {', '.join(f'{wr}%' for wr in recent_wr[:2])}。"
                f"請持續觀察。"
            )

    return alerts


# ---------------------------------------------------------------------------
# LINE 推播
# ---------------------------------------------------------------------------
def send_line_review(summaries: list[dict], alerts: list[str] | None = None) -> None:
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

    # 策略健康警報
    if alerts:
        lines.append("\n⚡ 策略健康警報：")
        for a in alerts:
            lines.append(a)

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

    # 策略健康警報
    alerts = _check_health(summaries)
    if alerts:
        print(f"\n⚡ 策略健康警報：")
        for a in alerts:
            print(f"  {a}")
    else:
        print(f"\n🟢 策略健康狀態正常")

    # 儲存覆盤結果 JSON（含警報）
    today_str = datetime.now(TW_TZ).date().isoformat()
    review_payload = {
        "review_date": today_str,
        "generated_at": datetime.now(TW_TZ).isoformat(timespec="seconds"),
        "markets": summaries,
        "alerts": alerts,
    }

    # 同時存 latest 與帶日期的歷史版本
    review_path = RESULT_DIR / "weekly_review.json"
    review_path.write_text(
        json.dumps(review_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    dated_path = REVIEW_DIR / f"weekly_review_{today_str}.json"
    dated_path.write_text(
        json.dumps(review_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n[+] 覆盤結果已儲存 -> {review_path}")
    print(f"[+] 歷史覆盤已存檔 -> {dated_path}")

    # LINE 推播
    if not args.no_line:
        send_line_review(summaries, alerts)
    else:
        print("  [i] 已跳過 LINE 推播 (--no-line)")

    print(f"\n{'='*40}")
    print(f"✅ 覆盤完成！")
    print(f"{'='*40}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
