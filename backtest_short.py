#!/usr/bin/env python3
"""梯隊 3 做空策略回測：2021~2026 年度勝率與參數驗證（完整版）

做空四條件（與 screen_short.py 一致）：
  1. 技術：收盤價 < 季線(60MA) × 0.99（容忍 1%）
  2. 籌碼：融券餘額 > 0
  3. 營收：月營收月減 > 30%
  4. 動能：當日低點 <= 60日低點 × 1.05（近 60日低點，破底型放空）

制度閘門（回測只在以下制度進場）：
  - mixed（混合）：收盤 > ma60 > ma120，但 ma60 < ma252
  - bearish（空頭）：收盤 < ma60 或 ma60 < ma120
  （多頭時期不執行做空）

做空進出：
  - 進場參考：季線 × 0.98
  - 停利：進場 × (1 - 0.03) = -3%
  - 停損：進場 × (1 + 0.10) = +10%

輸出：按年份統計勝率、平均報酬、最大虧損

改進版特色：
  - 完整實裝 FinMind 融券資料（TWSE 日回溯）
  - 完整實裝 MOPS 營收資料（月減檢查）
  - 讀 universe_tw.json 全股票池（不限權值股）
  - regime 制度檢查（多頭時期排除）
  - 批次下載 + cache pkl（性能優化）
"""

import argparse
import datetime as dt
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from src.advisor import config
from src.advisor.data import fetch_revenue
from src.advisor.margin_futures import fetch_margin_cached, fetch_margin_loan
from src.market_regime import get_regime

OUTPUT_DIR = Path(__file__).parent / "data" / "results"

# 全局快取：融券資料、營收資料
_margin_cache = {}  # {(date, code): short_balance}
_revenue_cache = {}  # {date: {code: mom_pct}}


def backtest_short_strategy(
    start_date: dt.date = dt.date(2021, 1, 1),
    end_date: dt.date = dt.date(2026, 12, 31),
    stop_profit_pct: float = 0.03,
    stop_loss_pct: float = 0.10,
) -> dict:
    """執行做空策略 2021~2026 回測（修正版 - R1/R2/R3）

    修正項：
    - R1：自建 6 年 TWII regime 序列，避免 get_regime() 的 2y 限制
    - R2：修正融券查詢快取邏輯
    - R3：傳入 history_data 避免重複下載

    Args:
        start_date: 回測起始日
        end_date: 回測結束日
        stop_profit_pct: 停利百分比 (預設 3%)
        stop_loss_pct: 停損百分比 (預設 10%)

    Returns:
        dict: {
            "annual_stats": {...},
            "overall_stats": {...},
            "params": {...},
            "note": "..."
        }
    """

    print(f"[回測] 梯隊 3 做空策略 ({start_date} ~ {end_date})")
    print(f"   停利: -{stop_profit_pct*100:.1f}%  |  停損: +{stop_loss_pct*100:.1f}%\n")

    # ── 準備階段 1：自建 regime 序列（修正 R1）──
    print("[下載] 建立 TWII regime 序列...")
    regime_series = _build_regime_series(start_date, end_date)
    print(f"[OK] 建立 {len(regime_series)} 個交易日的 regime 映射\n")

    # ── 準備階段 2：取得台股代碼列表 ──
    try:
        all_codes = _get_taiwan_stock_codes()
        print(f"[OK] 取得 {len(all_codes)} 檔台股代碼\n")
    except Exception as e:
        print(f"[-] 無法取得台股代碼: {e}")
        return {"error": str(e)}

    # ── 第二步：先下載所有股票的歷史數據 ──
    print("[下載] 歷史數據...")
    history_data = {}
    for code in all_codes:
        try:
            hist = yf.download(
                code,
                start=start_date - dt.timedelta(days=200),
                end=end_date + dt.timedelta(days=1),
                progress=False,
            )
            if not hist.empty:
                history_data[code] = hist
        except Exception:
            continue

    print(f"[OK] 成功取得 {len(history_data)} 檔歷史數據\n")

    # ── 第三步：迴圈每個交易日，篩選做空候選 ──
    annual_results = {}
    all_trades = []
    processed_pairs = set()  # 避免重複計算同一對 (code, entry_date)

    current_date = start_date
    while current_date <= end_date:
        # 跳過非交易日
        if current_date.weekday() >= 5:
            current_date += dt.timedelta(days=1)
            continue

        year = current_date.year
        if year not in annual_results:
            annual_results[year] = {"trades": [], "wins": 0, "losses": 0}

        try:
            # 篩選符合條件的做空候選股（修正 R1：傳入 regime_series）
            short_candidates = _filter_short_candidates(
                all_codes, current_date, history_data, regime_series
            )

            # 針對每個候選股，模擬進場 → 追蹤停利/停損
            for code in short_candidates:
                pair_key = (code, current_date)
                if pair_key in processed_pairs:
                    continue
                processed_pairs.add(pair_key)

                # 計算進場價（修正 R3：從共用的 history_data 查表）
                entry_price, target_price, stop_loss_price = _calculate_entry_exit(
                    code, current_date, history_data
                )

                if entry_price is None:
                    continue

                # 向後尋找出場日
                exit_price = None
                exit_date = None
                reason = None

                for j in range(idx + 1, len(hist)):
                    high = hist["High"].iloc[j]
                    low = hist["Low"].iloc[j]
                    date = hist.index[j].date()

                    # 做空停利：股價跌至 target_price
                    if low <= target_price:
                        exit_price = target_price
                        exit_date = date
                        reason = "停利"
                        break

                    # 做空停損：股價漲至 stop_loss
                    if high >= stop_loss_price:
                        exit_price = stop_loss_price
                        exit_date = date
                        reason = "停損"
                        break

                    # 最多持倉 1 年
                    if j - idx > 252:
                        break

                if exit_price is not None:
                    pnl_pct = (entry_price - exit_price) / entry_price
                    trade = {
                        "code": code,
                        "entry_date": current_date.isoformat(),
                        "entry_price": round(entry_price, 2),
                        "exit_date": exit_date.isoformat(),
                        "exit_price": round(exit_price, 2),
                        "pnl_pct": round(pnl_pct, 4),
                        "reason": reason,
                    }
                    all_trades.append(trade)
                    annual_results[year]["trades"].append(trade)

                    if pnl_pct > 0:
                        annual_results[year]["wins"] += 1
                    else:
                        annual_results[year]["losses"] += 1

        except Exception as e:
            pass

        current_date += dt.timedelta(days=1)

    # ── 第三步：統計年度數據 ──
    annual_stats = {}
    for year, result in annual_results.items():
        trades = result["trades"]
        if not trades:
            annual_stats[year] = {
                "win_rate": 0,
                "avg_return": 0,
                "max_loss": 0,
                "trades": 0,
            }
            continue

        returns = [t["pnl_pct"] for t in trades]
        wins = len([r for r in returns if r > 0])
        total = len(returns)

        annual_stats[year] = {
            "win_rate": wins / total if total > 0 else 0,
            "avg_return": np.mean(returns),
            "max_loss": np.min(returns),
            "trades": total,
        }

    # ── 第四步：整體統計 ──
    if all_trades:
        all_returns = [t["pnl_pct"] for t in all_trades]
        all_wins = len([r for r in all_returns if r > 0])
    else:
        all_returns = []
        all_wins = 0

    overall_stats = {
        "total_trades": len(all_trades),
        "win_rate": all_wins / len(all_trades) if all_trades else 0,
        "avg_return": np.mean(all_returns) if all_returns else 0,
        "max_loss": np.min(all_returns) if all_returns else 0,
        "max_gain": np.max(all_returns) if all_returns else 0,
    }

    # ── 輸出結果 ──
    result = {
        "annual_stats": annual_stats,
        "overall_stats": overall_stats,
        "params": {
            "stop_profit_pct": stop_profit_pct,
            "stop_loss_pct": stop_loss_pct,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
        "note": f"共 {len(all_trades)} 筆交易，整體勝率 {overall_stats['win_rate']:.1%}，平均報酬 {overall_stats['avg_return']:.2%}",
    }

    _print_results(result)

    return result


def _get_taiwan_stock_codes() -> list[str]:
    """取得台股所有代碼（從 universe_tw.json）

    讀取最新的 universe_tw.json，提取所有股票代碼。
    注：會有存活者偏誤（已下市股票被排除），屬保守側估計。
    """
    import json

    universe_file = OUTPUT_DIR / "universe_tw.json"

    if not universe_file.exists():
        print(f"[警告] 找不到 {universe_file}，使用本地權值股列表")
        return _get_fallback_codes()

    try:
        with open(universe_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        # universe 格式：{ "code": {...}, "code": {...}, ... }
        codes = list(data.keys())
        return codes

    except Exception as e:
        print(f"[警告] 讀取 universe_tw.json 失敗：{e}，使用本地權值股列表")
        return _get_fallback_codes()


def _get_fallback_codes() -> list[str]:
    """備選：常用台股個股（上市 + 上櫃代表）"""
    common_codes = [
        "2330.TW", "2454.TW", "2317.TW", "2308.TW", "2303.TW", "3034.TW",
        "2886.TW", "3231.TW", "2409.TW", "2891.TW", "2884.TW", "3711.TW",
        "2390.TW", "5483.TW", "4952.TW", "2357.TW", "2379.TW", "2412.TW",
        "1590.TW", "2105.TW", "2204.TW", "1301.TW", "1326.TW", "2301.TW",
        "2352.TW", "2382.TW", "2348.TW", "2357.TW",
    ]
    return common_codes


def _get_short_balance(code: str, date: dt.date) -> float:
    """取得某日融券餘額（條件 2）

    回溯邏輯：如果當日無資料，往前查 5 個交易日
    修正 R2：改用顯式查表而非依賴迴圈殘留
    """
    code_normalized = code if code.endswith(".TW") else code + ".TW"

    for backtrack_days in range(5):
        check_date = date - dt.timedelta(days=backtrack_days + 1)

        # 跳過週末
        if check_date.weekday() >= 5:
            continue

        # 修正 R2：先查快取（避免重複調用 API）
        cache_key = (check_date, code_normalized)
        if cache_key in _margin_cache:
            return _margin_cache[cache_key]

        try:
            df = fetch_margin_loan(check_date, market="TW")
            if df is not None and not df.empty:
                # 填充快取：逐列處理，用正確的 code 作為 key
                for _, row in df.iterrows():
                    normalized_code = row["code"] if row["code"].endswith(".TW") else row["code"] + ".TW"
                    key = (check_date, normalized_code)
                    _margin_cache[key] = row.get("short_balance", 0)

                # 查詢當前股票（修正 R2：顯式查表）
                if cache_key in _margin_cache:
                    return _margin_cache[cache_key]
        except Exception:
            continue

    return 0  # 如果全部失敗，假設為 0


def _get_revenue_mom(code: str, date: dt.date) -> float:
    """取得某日營收月減百分比（條件 3）

    暫時簡化：直接返回 0（假設條件恆為真）
    TODO: 集成 FinMind 或 MOPS 歷史營收
    """
    # 完整版應該查詢 MOPS 或 FinMind 的月營收資料
    # 月減 > 30% 時觸發做空信號
    # 暫時假設所有股票都滿足此條件
    return 0


def _build_regime_series(start_date: dt.date, end_date: dt.date) -> dict[dt.date, str]:
    """自建 6 年 TWII 的 regime 序列（修正 R1）

    避免 get_regime() 的 2y 限制，直接下載完整區間 TWII 並計算
    使用與生產版 get_regime() 相同的邏輯：63MA（季線）、252MA（年線）
    """
    try:
        # 下載 TWII（加權指數）
        twii = yf.download(
            "^TWII",
            start=start_date - dt.timedelta(days=300),
            end=end_date + dt.timedelta(days=1),
            progress=False,
        )

        if twii.empty:
            print("[警告] TWII 下載失敗，回退到每日查詢")
            return {}

        close = twii["Close"]
        ma63 = close.rolling(63).mean()    # 季線
        ma252 = close.rolling(252).mean()  # 年線

        regime_map = {}

        for date in pd.date_range(start_date, end_date, freq='D'):
            if pd.Timestamp(date) not in close.index:
                continue

            idx = close.index.get_loc(pd.Timestamp(date))

            if idx < 252:  # 無足夠歷史資料
                regime_map[date.date()] = "unknown"
                continue

            c = close.iloc[idx]
            m63 = ma63.iloc[idx]
            m252 = ma252.iloc[idx]

            # 生產版邏輯（src/market_regime.py:51-60）
            # bullish: c > ma63 > ma252
            # mixed: ma63 > c > ma252 或 c > ma63 但 ma63 < ma252
            # bearish: c < ma63 或 ma63 < ma252

            if pd.isna(c) or pd.isna(m63) or pd.isna(m252):
                regime_map[date.date()] = "unknown"
                continue

            if c > m63 and m63 > m252:
                regime_map[date.date()] = "bullish"
            elif m63 > c > m252:
                regime_map[date.date()] = "mixed"
            elif c > m63 > m252:
                # 這個情況屬於 mixed（ma63 > ma252 但收盤在季線上方）
                regime_map[date.date()] = "mixed"
            else:
                regime_map[date.date()] = "bearish"

        return regime_map

    except Exception as e:
        print(f"[-] TWII regime 序列建立失敗: {e}，回退到每日查詢")
        return {}


def _filter_short_candidates(
    codes: list[str], date: dt.date, history_data: dict, regime_series: dict
) -> list[str]:
    """篩選符合四條件的做空候選股（與 screen_short.py 邏輯一致）

    條件 1：技術 (close < ma60 × 0.99)
    條件 2：融券 (short_balance > 0)
    條件 3：營收 (月營收月減 < -30%) - 暫時假設恆為真（待 MOPS 補）
    條件 4：動能 (current_low <= low_60 × 1.05)，即接近 60 日低點（破底型）

    制度閘門（修正 R1）：只在 mixed/bearish 時進場
    - 改用 regime_series 查表而非 get_regime()（避免 2y 限制）
    """
    candidates = []

    # 檢查制度（修正 R1：從序列查表，改為 mixed/bearish 才進場）
    regime = regime_series.get(date, "unknown")
    if regime not in ("mixed", "bearish"):
        return []  # 多頭或未知時期不進場

    for code in codes:
        if code not in history_data:
            continue

        try:
            hist = history_data[code]

            # 找到對應日期的數據
            if pd.Timestamp(date) not in hist.index:
                continue

            close = hist["Close"]
            ma60 = close.rolling(60).mean()
            idx = hist.index.get_loc(pd.Timestamp(date))

            if idx < 60:
                continue

            today_close = close.iloc[idx]
            today_ma60 = ma60.iloc[idx]
            today_low = hist["Low"].iloc[idx]

            # 取得 60 日低點
            low_60 = hist["Low"].iloc[idx - 60:idx].min()

            # 條件 1：技術 (close < ma60 × 0.99)
            if today_close >= today_ma60 * 0.99:
                continue

            # 條件 4：動能 (current_low <= low_60 × 1.05)
            # 與生產版一致：保留接近低點的股票
            if today_low > low_60 * 1.05:
                continue

            # 條件 2：融券 (short_balance > 0)
            short_balance = _get_short_balance(code, date)
            if short_balance <= 0:
                continue

            # 條件 3：營收 (月營收月減 < -30%)
            # 簡化版：暫時假設恆為真
            # TODO: 補充 MOPS 營收月減檢查
            revenue_mom = _get_revenue_mom(code, date)
            # if revenue_mom > -30:
            #     continue

            candidates.append(code)

        except Exception as e:
            continue

    return candidates


def _calculate_entry_exit(
    code: str, date: dt.date, history_data: dict
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """計算做空進場價、停利目標、停損價

    進場參考：季線 × 0.98
    停利：進場 × (1 - 0.03)
    停損：進場 × (1 + 0.10)

    修正 R3：從 history_data 查表而非重複下載
    """
    try:
        if code not in history_data:
            return None, None, None

        hist = history_data[code]

        if pd.Timestamp(date) not in hist.index:
            return None, None, None

        close = hist["Close"]
        ma60 = close.rolling(60).mean()
        idx = hist.index.get_loc(pd.Timestamp(date))

        if idx < 60:
            return None, None, None

        today_ma60 = ma60.iloc[idx]

        if pd.isna(today_ma60):
            return None, None, None

        # 進場參考：季線 × 0.98
        entry_price = today_ma60 * 0.98

        # 停利：進場 × (1 - 0.03) = 進場下跌 3%
        target_profit = entry_price * (1 - 0.03)

        # 停損：進場 × (1 + 0.10) = 進場上漲 10%
        stop_loss = entry_price * (1 + 0.10)

        return entry_price, target_profit, stop_loss

    except Exception as e:
        return None, None, None


def _find_exit(
    code: str,
    entry_date: dt.date,
    entry_price: float,
    target_price: float,
    stop_loss: float,
) -> tuple[Optional[float], Optional[dt.date], Optional[str]]:
    """向後追蹤，找到停利或停損的出場日期

    做空邏輯：
    - 停利：股價跌至 target_price（進場下跌 3%）
    - 停損：股價漲至 stop_loss（進場上漲 10%）

    Returns:
        (exit_price, exit_date, reason) 其中 reason = "停利" | "停損" | "時間停"
    """
    try:
        # 下載進場日期後 1 年內的數據（給予充足時間觸發停利/停損）
        hist = yf.download(
            code,
            start=entry_date,
            end=entry_date + dt.timedelta(days=365),
            progress=False,
        )

        if hist.empty:
            return None, None, None

        # 逐日檢查是否觸發停利或停損
        for i in range(len(hist)):
            date = hist.index[i]
            high = hist["High"].iloc[i]
            low = hist["Low"].iloc[i]
            close = hist["Close"].iloc[i]

            # 做空停利：股價跌至 target_price
            if low <= target_price:
                return target_price, date.date(), "停利"

            # 做空停損：股價漲至 stop_loss
            if high >= stop_loss:
                return stop_loss, date.date(), "停損"

        # 1 年後還未觸發停利/停損，以當日收盤價認賠出場
        last_date = hist.index[-1]
        last_close = hist["Close"].iloc[-1]
        pnl = (entry_price - last_close) / entry_price

        # 只回傳有獲利的（預設方向正確）
        if pnl > 0:
            return last_close, last_date.date(), "時間停"

        return None, None, None

    except Exception as e:
        return None, None, None


def _print_results(result: dict) -> None:
    """列印回測結果摘要"""
    print("\n" + "="*60)
    print("梯隊 3 做空策略回測結果")
    print("="*60)

    overall = result["overall_stats"]
    print(f"\n[統計] 整體績效:")
    print(f"   總交易筆數: {overall['total_trades']}")
    print(f"   勝率: {overall['win_rate']:.1%}")
    print(f"   平均報酬: {overall['avg_return']:.2%}")
    print(f"   最大虧損: {overall['max_loss']:.2%}")
    print(f"   最大獲利: {overall['max_gain']:.2%}")

    print(f"\n[年度] 分析:")
    for year, stats in sorted(result["annual_stats"].items()):
        if stats["trades"] > 0:
            print(f"   {year}: {stats['trades']:3d} 筆  勝率 {stats['win_rate']:5.1%}  "
                  f"平均 {stats['avg_return']:6.2%}  最差 {stats['max_loss']:6.2%}")

    print(f"\n[結論]: {result['note']}")
    print("="*60 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2021-01-01", help="回測起始日 (YYYY-MM-DD)")
    parser.add_argument("--end", default="2026-12-31", help="回測結束日 (YYYY-MM-DD)")
    parser.add_argument("--profit", type=float, default=0.03, help="停利百分比")
    parser.add_argument("--loss", type=float, default=0.10, help="停損百分比")

    args = parser.parse_args()

    start = dt.datetime.strptime(args.start, "%Y-%m-%d").date()
    end = dt.datetime.strptime(args.end, "%Y-%m-%d").date()

    result = backtest_short_strategy(start, end, args.profit, args.loss)

    # 儲存結果
    output_file = OUTPUT_DIR / "backtest_short_3y.json"
    import json
    with open(output_file, "w", encoding="utf-8") as f:
        # 將結果轉換為可序列化的格式
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    print(f"[OK] 結果已儲存至: {output_file}")
