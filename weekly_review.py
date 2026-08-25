"""週末自動覆盤：比對當週選股結果與最新收盤價，計算策略績效並推播 LINE。

主 KPI 是 Top3 命中率對股票池同窗命中率的超額（P−U），不是絕對勝率 50%。
合集（一週每日 Top30 去重）只當附註。選股邏輯不在此檔。

用法:
    python weekly_review.py              # 完整流程（含 LINE 推播）
    python weekly_review.py --no-line    # 只計算不推播（本機測試用）
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import re
import sys
from datetime import date, datetime, timedelta
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
CACHE_DIR = Path(__file__).parent / "src" / "advisor" / "cache"
TOP_N = 30  # 追蹤前 N 檔（合集附註）
ALERT_WEEKS = 4  # 健康警報回望週數
REVIEW_DIR = RESULT_DIR / "reviews"  # 歷史覆盤存放目錄
INDEX_CODE = {"TW": "0050", "US": "SPY"}
_YF_CHUNK = 80

# 嘗試載入 .env（本機開發用）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_SELECT_WINDOW_RE = re.compile(r"\((\d{2})-(\d{2}) ~ (\d{2})-(\d{2})\)")
_HIST_CACHE: dict | None = None


# ---------------------------------------------------------------------------
# 尺：Top3 命中率 P vs 股票池同窗命中率 U
# ---------------------------------------------------------------------------
def primary_win_rate(ms: dict) -> float | None:
    """行動對象的命中率：有 Top3 分段就用它，否則退回合集。"""
    top3 = (ms.get("segments") or {}).get("top3")
    if top3 and top3.get("total"):
        return top3["win_rate"]
    return ms.get("win_rate")


def universe_hit_rate(ms: dict) -> float | None:
    bench = ms.get("benchmark") or {}
    return bench.get("universe_hit_rate")


def lift_pp(ms: dict) -> float | None:
    """P−U（百分點）。缺任一端則 None。"""
    p, u = primary_win_rate(ms), universe_hit_rate(ms)
    if p is None or u is None:
        return None
    return round(p - u, 1)


def lost_to_universe(ms: dict) -> bool | None:
    """True = 沒有選股能力（P≤U）。缺資料回 None。"""
    v = lift_pp(ms)
    if v is None:
        return None
    return v <= 0


def parse_select_window(select_date: str, review_date: str) -> tuple[date, date] | None:
    """'本週 (08-17 ~ 08-21)' + review_date → (start, end)。"""
    m = _SELECT_WINDOW_RE.search(select_date or "")
    if not m:
        return None
    year = int(str(review_date)[:4])
    start = date(year, int(m.group(1)), int(m.group(2)))
    end = date(year, int(m.group(3)), int(m.group(4)))
    if start > end:  # 跨年：start 在前一年
        start = date(year - 1, start.month, start.day)
    return start, end


def _value_on_or_before(series: pd.Series, d: date) -> float | None:
    if series is None or series.empty:
        return None
    ts = pd.Timestamp(d)
    try:
        sub = series.loc[:ts].dropna()
    except Exception:
        return None
    if sub.empty:
        return None
    v = float(sub.iloc[-1])
    return v if v == v and v > 0 else None


def _hit_rate_from_closes(
    closes: dict[str, pd.Series], start: date, end: date
) -> dict:
    """closes: code → Close series。回傳股票池同窗命中統計。"""
    up = n = 0
    for s in closes.values():
        c0 = _value_on_or_before(s, start)
        c1 = _value_on_or_before(s, end)
        if c0 is None or c1 is None:
            continue
        n += 1
        if c1 > c0:
            up += 1
    return {
        "universe_n": n,
        "universe_up": up,
        "universe_hit_rate": round(up / n * 100, 1) if n else None,
        "window": [start.isoformat(), end.isoformat()],
    }


def _latest_hist_pkl() -> Path | None:
    paths = sorted(CACHE_DIR.glob("hist_2y_*.pkl"))
    return paths[-1] if paths else None


def _load_hist_cache() -> dict:
    global _HIST_CACHE
    if _HIST_CACHE is not None:
        return _HIST_CACHE
    path = _latest_hist_pkl()
    if path is None:
        _HIST_CACHE = {}
        return _HIST_CACHE
    try:
        with open(path, "rb") as f:
            _HIST_CACHE = pickle.load(f)
        print(f"  [>] 股票池價格用快取 {path.name}", flush=True)
    except Exception as e:
        print(f"  [!] 讀快取失敗（改抓 yfinance）: {e}", flush=True)
        _HIST_CACHE = {}
    return _HIST_CACHE


def _load_universe_meta(market: str) -> list[dict]:
    path = RESULT_DIR / f"universe_{market.lower()}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data.get("stocks") or []


def _tw_ticker(stock_id: str, listing: str) -> str:
    return f"{stock_id}.TWO" if listing == "上櫃" else f"{stock_id}.TW"


def _closes_from_cache(codes: list[str]) -> dict[str, pd.Series]:
    hist = _load_hist_cache()
    out: dict[str, pd.Series] = {}
    for code in codes:
        df = hist.get(code)
        if isinstance(df, pd.DataFrame) and "Close" in df.columns and not df.empty:
            out[code] = df["Close"]
    return out


def _closes_from_yf(tickers: list[str], start: date, end: date) -> dict[str, pd.Series]:
    """yfinance 分批抓收盤。ticker 含後綴（2330.TW）。回傳 ticker→Close。"""
    if not tickers:
        return {}
    span_start = start - timedelta(days=10)
    span_end = end + timedelta(days=3)
    out: dict[str, pd.Series] = {}
    print(f"  [>] yfinance 抓股票池 {len(tickers)} 檔（{span_start} ~ {span_end}）...", flush=True)
    for i in range(0, len(tickers), _YF_CHUNK):
        chunk = tickers[i : i + _YF_CHUNK]
        try:
            df = yf.download(
                chunk,
                start=span_start.isoformat(),
                end=(span_end + timedelta(days=1)).isoformat(),
                auto_adjust=True,
                group_by="ticker",
                progress=False,
                threads=True,
            )
        except Exception as e:
            print(f"  [!] yfinance 批次失敗 ({i}): {e}", flush=True)
            continue
        if df is None or df.empty:
            continue
        for t in chunk:
            try:
                if len(chunk) == 1:
                    s = df["Close"]
                else:
                    s = df[t]["Close"]
                s = s.dropna()
                if not s.empty:
                    out[t] = s
            except (KeyError, TypeError):
                pass
    return out


def build_benchmark(market: str, start: date, end: date, allow_yf: bool = True) -> dict | None:
    """股票池同窗命中率 + 指數報酬。優先本地 hist pkl，否則 yfinance。"""
    meta = _load_universe_meta(market)
    if not meta:
        print(f"  [!] 找不到 universe_{market.lower()}.json，跳過股票池對照", flush=True)
        return None

    codes = [s["stock_id"] for s in meta if s.get("stock_id")]
    closes = _closes_from_cache(codes)
    source = "cache"
    cache_ok = len(closes) >= max(50, int(len(codes) * 0.5))
    if not cache_ok:
        if not allow_yf:
            if not closes:
                return None
        else:
            if market == "TW":
                tickers = [
                    _tw_ticker(s["stock_id"], s.get("市場", "上市"))
                    for s in meta if s.get("stock_id")
                ]
                yf_map = {
                    _tw_ticker(s["stock_id"], s.get("市場", "上市")): s["stock_id"]
                    for s in meta if s.get("stock_id")
                }
            else:
                tickers = list(codes)
                yf_map = {c: c for c in codes}
            yf_closes = _closes_from_yf(tickers, start, end)
            closes = {yf_map.get(t, t): s for t, s in yf_closes.items()}
            source = "yfinance"

    stats = _hit_rate_from_closes(closes, start, end)
    stats["source"] = source

    idx_code = INDEX_CODE[market]
    idx_series = closes.get(idx_code)
    if idx_series is None and allow_yf:
        yf_idx = "0050.TW" if market == "TW" else "SPY"
        extra = _closes_from_yf([yf_idx], start, end)
        idx_series = extra.get(yf_idx)
    c0 = _value_on_or_before(idx_series, start) if idx_series is not None else None
    c1 = _value_on_or_before(idx_series, end) if idx_series is not None else None
    stats["index"] = idx_code
    stats["index_return"] = round((c1 / c0 - 1) * 100, 2) if c0 and c1 else None
    if stats.get("universe_hit_rate") is None:
        return None
    return stats


# ---------------------------------------------------------------------------
# 核心邏輯
# ---------------------------------------------------------------------------
def _load_weekly_data(market: str) -> dict | None:
    """讀取過去 7 天內該市場的所有選股結果，並彙整出唯一清單與最早入榜的基準價格。"""
    today = datetime.now(TW_TZ).date()
    # 從 7 天前到 1 天前 (確保舊的在前，新的在後，這樣 earliest 才會是最早入榜那天的價錢)
    past_7_days = [(today - timedelta(days=i)).isoformat() for i in range(7, 0, -1)]
    
    unique_stocks = {}
    
    for date_str in past_7_days:
        path = RESULT_DIR / f"{date_str}_{market.lower()}.json"
        if not path.exists():
            continue
            
        with open(path, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                screened = data.get("screened", [])
                for rank, s in enumerate(screened[:TOP_N], start=1):
                    stock_id = s["stock_id"]
                    if stock_id not in unique_stocks:
                        unique_stocks[stock_id] = {
                            "stock_id": stock_id,
                            "stock_name": s.get("stock_name", ""),
                            "close": s.get("close", 0),
                            "總分": s.get("總分", 0),
                            "市場": s.get("市場", ""),
                            "first_select_date": date_str,
                            # 首次入榜當日名次與產業上限標記（分段績效統計用）
                            "first_rank": rank,
                            "capped": s.get("capped"),
                        }
            except Exception as e:
                print(f"  [!] 讀取 {path} 失敗: {e}")
                
    if not unique_stocks:
        print(f"  [!] 找不到過去 7 天的 {market} 資料")
        return None
        
    # 找出實際有資料的第一天和最後一天
    valid_dates = [s["first_select_date"] for s in unique_stocks.values()]
    start_str = min(valid_dates)[5:] if valid_dates else "unknown"
    end_str = max(valid_dates)[5:] if valid_dates else "unknown"
        
    return {
        "date": f"本週 ({start_str} ~ {end_str})",
        "screened": list(unique_stocks.values())
    }


def _build_tickers(screened: list[dict], market: str) -> tuple[list[str], dict]:
    """從選股清單建立 yfinance ticker 列表與對照表。"""
    tickers = []
    mapping = {}
    for s in screened:  # 不要再切 [:TOP_N]，因為 _load_weekly_data 已經篩選過每天的前 N 名了
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
            "first_rank": s.get("first_rank"),
            "capped": s.get("capped"),
        }
    return tickers, mapping


def _segment_stats(df: pd.DataFrame, mask: pd.Series) -> dict | None:
    """計算一個分段（遮罩）的勝率與平均報酬。樣本為 0 時回傳 None。"""
    seg = df[mask]
    if seg.empty:
        return None
    total = len(seg)
    win_count = int((seg["return_pct"] > 0).sum())
    return {
        "total": total,
        "win_count": win_count,
        "win_rate": round(win_count / total * 100, 1),
        "avg_return": round(seg["return_pct"].mean(), 2),
    }


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


def _format_segments(segments: dict) -> str:
    """分段統計摘要成單行文字，如 Top3 67%(+1.2%) / Top10 55%(+0.4%) / 全部 45%(-0.8%)。"""
    labels = [("top3", "Top3"), ("top10", "Top10"), ("all", "全部")]
    parts = []
    for key, label in labels:
        s = segments.get(key)
        if s:
            parts.append(f"{label} {s['win_rate']}%({s['avg_return']:+.1f}%)")
    return " / ".join(parts)


def _format_capped(capped_stats: dict) -> str:
    """capped 分組摘要成單行文字。"""
    parts = []
    for key, label in [("uncapped", "主榜"), ("capped", "被延後")]:
        s = capped_stats.get(key)
        if s:
            parts.append(f"{label} {s['win_rate']}%({s['avg_return']:+.1f}%, n={s['total']})")
    return " vs ".join(parts)


def review_market(market: str) -> dict | None:
    """對單一市場執行覆盤，回傳績效摘要 dict。"""
    data = _load_weekly_data(market)
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
            "first_rank": mapping[ticker]["first_rank"],
            "capped": mapping[ticker]["capped"],
        })

    if not results:
        print(f"  [!] {market} 無法取得任何價格資料")
        return None

    res_df = pd.DataFrame(results).sort_values("return_pct", ascending=False)
    avg_ret = round(res_df["return_pct"].mean(), 2)
    win_count = int((res_df["return_pct"] > 0).sum())
    total = len(res_df)
    win_rate = round(win_count / total * 100, 1)

    # 分段統計：量測對象對齊實際行動對象（LINE Top 3 / 排行榜前段）
    rank = res_df["first_rank"].fillna(TOP_N + 1)
    segments = {
        "top3": _segment_stats(res_df, rank <= 3),
        "top10": _segment_stats(res_df, rank <= 10),
        "all": _segment_stats(res_df, rank <= TOP_N + 1),
    }

    # capped 分組績效：驗證產業集中度上限的實際效果（舊資料無此欄位則為 None）
    capped_stats = None
    if res_df["capped"].notna().any():
        capped_stats = {
            "capped": _segment_stats(res_df, res_df["capped"] == True),  # noqa: E712
            "uncapped": _segment_stats(res_df, res_df["capped"] == False),  # noqa: E712
        }

    # 最強 / 最弱
    top3 = res_df.head(3)[["name", "return_pct"]].values.tolist()
    bottom3 = res_df.tail(3)[["name", "return_pct"]].values.tolist()
    # 只保留虧損的
    bottom3 = [x for x in bottom3 if x[1] < 0]

    review_day = datetime.now(TW_TZ).date()
    window = parse_select_window(select_date, review_day.isoformat())
    benchmark = None
    if window:
        benchmark = build_benchmark(market, window[0], window[1])

    summary = {
        "market": market,
        "select_date": select_date,
        "review_date": review_day.isoformat(),
        "total": total,
        "win_count": win_count,
        "win_rate": win_rate,
        "avg_return": avg_ret,
        "segments": segments,
        "capped_stats": capped_stats,
        "benchmark": benchmark,
        "lift_top3": None,
        "top3": [{"name": n, "ret": r} for n, r in top3],
        "bottom3": [{"name": n, "ret": r} for n, r in bottom3],
        "details": results,
    }
    summary["lift_top3"] = lift_pp(summary)

    # 終端機輸出：主 KPI 是 Top3 vs 股票池，合集當附註
    market_label = "🇹🇼 台股" if market == "TW" else "🇺🇸 美股"
    print(f"\n  {market_label} 覆盤結果 (選股日: {select_date})")
    t3 = segments.get("top3")
    if t3:
        print(f"  🎯 Top3 命中: {t3['win_rate']}% ({t3['win_count']}/{t3['total']})  "
              f"平均 {t3['avg_return']:+.1f}%")
    if benchmark and benchmark.get("universe_hit_rate") is not None:
        idx_s = ""
        if benchmark.get("index_return") is not None:
            idx_s = f"  {benchmark['index']} {benchmark['index_return']:+.1f}%"
        lift_s = f"{summary['lift_top3']:+.1f}pp" if summary["lift_top3"] is not None else "n/a"
        print(f"  📊 股票池同窗: {benchmark['universe_hit_rate']}% "
              f"({benchmark['universe_up']}/{benchmark['universe_n']} 上漲)  "
              f"超額 {lift_s}{idx_s}")
    print(f"  📎 合集（附註）: {win_rate}% ({win_count}/{total})  平均 {avg_ret:+.1f}%")
    print(f"  🎯 分段: {_format_segments(segments)}")
    if capped_stats:
        print(f"  🏭 產業上限: {_format_capped(capped_stats)}")
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


def _attach_benchmark_if_missing(ms: dict) -> None:
    """歷史覆盤沒有 benchmark 時，用同一檔價格快取補算（不回寫舊檔）。"""
    if universe_hit_rate(ms) is not None:
        return
    window = parse_select_window(ms.get("select_date", ""), ms.get("review_date", ""))
    market = ms.get("market")
    if not window or market not in INDEX_CODE:
        return
    bench = build_benchmark(market, window[0], window[1], allow_yf=False)
    if bench:
        ms["benchmark"] = bench


def _check_health(current_summaries: list[dict]) -> list[str]:
    """近幾週 Top3 是否連續輸給股票池（P≤U）。不再用絕對勝率 50%。"""
    past_reviews = _load_past_reviews()
    if not past_reviews:
        return []

    alerts = []
    for market in ["TW", "US"]:
        market_label = "🇹🇼 台股" if market == "TW" else "🇺🇸 美股"
        weeks: list[dict] = []
        seen_weeks: set[str] = set()

        current = next((s for s in current_summaries if s["market"] == market), None)
        if current:
            weeks.append(current)
            today_iso = datetime.now(TW_TZ).date().isocalendar()
            seen_weeks.add(f"{today_iso[0]}-W{today_iso[1]}")

        for review in past_reviews:
            rev_date_str = review.get("review_date")
            if not rev_date_str:
                continue
            try:
                dt = datetime.strptime(rev_date_str, "%Y-%m-%d").date()
                iso_week = f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]}"
            except ValueError:
                continue
            if iso_week in seen_weeks:
                continue
            for ms in review.get("markets", []):
                if ms["market"] == market:
                    weeks.append(ms)
                    seen_weeks.add(iso_week)

        outcomes = []  # (lost, p, u, lift)
        for ms in weeks:
            _attach_benchmark_if_missing(ms)
            lost = lost_to_universe(ms)
            if lost is None:
                continue
            p = primary_win_rate(ms)
            u = universe_hit_rate(ms)
            outcomes.append((lost, p, u, lift_pp(ms)))

        if len(outcomes) < 2:
            continue

        recent = outcomes[:ALERT_WEEKS]
        lift_txt = ", ".join(
            f"P {p}% / U {u}% ({lift:+.1f}pp)" for _, p, u, lift in recent
        )

        if len(recent) >= 4 and all(lost for lost, *_ in recent[:4]):
            alerts.append(
                f"🔴 {market_label} 連續 4 週 Top3 命中率輸給股票池（P≤U）！"
                f"近 4 週: {lift_txt}。"
                f"選股沒抓到相對強勢，不是「勝率低於 50%」本身。"
            )
        elif len(recent) >= 2 and all(lost for lost, *_ in recent[:2]):
            alerts.append(
                f"🟡 {market_label} 連續 2 週 Top3 命中率輸給股票池（P≤U），"
                f"近 2 週: {', '.join(f'{lift:+.1f}pp' for *_, lift in recent[:2])}。"
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
        t3 = (s.get("segments") or {}).get("top3")
        bench = s.get("benchmark") or {}
        top_str = "、".join(f"{t['name']} {t['ret']:+.1f}%" for t in s["top3"])

        if t3:
            head = (
                f"🎯 Top3 命中: {t3['win_rate']}% ({t3['win_count']}/{t3['total']})  "
                f"平均 {t3['avg_return']:+.1f}%"
            )
        else:
            head = (
                f"✅ 合集勝率: {s['win_rate']}% ({s['win_count']}/{s['total']})\n"
                f"📈 平均報酬: {s['avg_return']:+.1f}%"
            )

        block = f"\n📍 {market_label} (選股日: {s['select_date']})\n{head}"

        if bench.get("universe_hit_rate") is not None:
            lift = s.get("lift_top3")
            if lift is None:
                lift = lift_pp(s)
            lift_s = f"{lift:+.1f}pp" if lift is not None else "n/a"
            idx_s = ""
            if bench.get("index_return") is not None:
                idx_s = f"  {bench['index']} {bench['index_return']:+.1f}%"
            block += (
                f"\n📊 股票池同窗: {bench['universe_hit_rate']}% "
                f"({bench.get('universe_up', '?')}/{bench.get('universe_n', '?')} 上漲)  "
                f"超額 {lift_s}{idx_s}"
            )

        block += f"\n📎 合集（附註）: {s['win_rate']}% ({s['win_count']}/{s['total']})  平均 {s['avg_return']:+.1f}%"
        block += f"\n🏆 最強: {top_str}"

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
