"""歷史回測：過去三年台股與美股的曝險水位變化分析

分析內容：
1. 每日計算三因子曝險水位（趨勢 + 寬度 + 波動率）
2. 統計 100% 水位出現的頻率與時期
3. 分析水位分佈，找出邏輯是否合理
4. 輸出完整報告

用法:
    python tests/backtest_exposure_3y.py            # 水位分佈分析
    python tests/backtest_exposure_3y.py --compare  # 固定 vs 自適應 VOL_TARGET 報酬比較
    python tests/backtest_exposure_3y.py --compare --period 5y
"""
import argparse
import sys
sys.path.insert(0, ".")

import numpy as np
import pandas as pd
import yfinance as yf
import json
from datetime import datetime
from src.advisor import config
from src.advisor import market as adv_market

# ---------------------------------------------------------------------------
# 核心計算（複製自 market.py，但支援 3 年回溯）
# ---------------------------------------------------------------------------
def calc_trend(close: pd.Series) -> pd.Series:
    """趨勢分級：三個條件各佔權重，0–1"""
    ma60 = close.rolling(60).mean()
    ma120 = close.rolling(120).mean()
    trend = ((close > ma60) * 0.4
             + (ma60 > ma120) * 0.3
             + (ma60 > ma60.shift(10)) * 0.3)
    return trend


def calc_breadth(close_panel: pd.DataFrame) -> pd.Series:
    """市場寬度：每日「收盤站上季線」的個股比例（0–1）"""
    ma60 = close_panel.rolling(60).mean()
    above = (close_panel > ma60).sum(axis=1)
    valid = close_panel.notna().sum(axis=1).clip(lower=1)
    return above / valid


def calc_exposure(index_close: pd.Series, breadth: pd.Series,
                  vol_target: float | pd.Series = config.VOL_TARGET) -> pd.DataFrame:
    """計算完整的曝險序列，回傳包含所有因子的 DataFrame

    vol_target 可為固定常數或序列（自適應目標）。
    """
    trend = calc_trend(index_close)

    b_norm = ((breadth.reindex(index_close.index).ffill() - config.BREADTH_LOW)
              / (config.BREADTH_HIGH - config.BREADTH_LOW)).clip(0, 1)

    base = config.EXP_W_TREND * trend + config.EXP_W_BREADTH * b_norm

    realized = index_close.pct_change().rolling(20).std() * np.sqrt(252)
    vol_scale = (vol_target / realized).clip(upper=1.0)

    expo_raw = (base * vol_scale).clip(0, 1)
    expo = (expo_raw / config.EXP_STEP).round() * config.EXP_STEP

    return pd.DataFrame({
        "exposure": expo,
        "trend": trend,
        "breadth_raw": breadth.reindex(index_close.index).ffill(),
        "breadth_norm": b_norm,
        "realized_vol": realized,
        "vol_scale": vol_scale,
        "base": base,
    }).dropna()


# ---------------------------------------------------------------------------
# 資料下載
# ---------------------------------------------------------------------------
def download_index(symbol: str, period: str = "3y") -> pd.Series:
    """下載指數收盤價"""
    print(f"  [>] 下載 {symbol} 指數資料 ({period})...", flush=True)
    df = yf.download(symbol, period=period, auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df["Close"].dropna()


def download_breadth_panel(tickers: list, period: str = "3y") -> pd.DataFrame:
    """下載一籃子個股收盤價，用於計算市場寬度"""
    print(f"  [>] 下載 {len(tickers)} 檔個股資料計算市場寬度...", flush=True)
    # 分批下載
    chunk_size = 50
    panels = []
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i:i+chunk_size]
        try:
            df = yf.download(chunk, period=period, auto_adjust=True, progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                close = df["Close"]
            else:
                close = df[["Close"]]
                close.columns = [chunk[0]]
            panels.append(close)
        except Exception as e:
            print(f"    [!] 批次 {i//chunk_size + 1} 下載失敗: {e}")
            continue

    if not panels:
        raise RuntimeError("無法下載任何個股資料")
    
    result = pd.concat(panels, axis=1)
    print(f"  [+] 成功下載 {result.shape[1]} 檔個股，{result.shape[0]} 個交易日", flush=True)
    return result


def get_sp500_tickers() -> list:
    """取得 S&P 500 成分股代碼"""
    try:
        table = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
        return table["Symbol"].str.replace(".", "-", regex=False).tolist()
    except Exception:
        # fallback: 使用主要權值股
        return ["AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "META", "TSLA", "BRK-B",
                "UNH", "XOM", "JNJ", "JPM", "V", "PG", "MA", "HD", "CVX", "MRK",
                "ABBV", "LLY", "PEP", "KO", "AVGO", "COST", "WMT", "TMO", "MCD",
                "CSCO", "ACN", "ABT", "DHR", "NEE", "LIN", "TXN", "PM", "UNP",
                "RTX", "ORCL", "HON", "LOW", "AMGN", "COP", "IBM", "INTC", "AMD",
                "QCOM", "CAT", "BA", "GE", "SBUX"]


def get_tw_tickers() -> list:
    """取得台股主要成分股代碼"""
    # 使用 0050 成分股 + 其他主要個股作為代表
    return [
        "2330.TW", "2454.TW", "2317.TW", "2382.TW", "2308.TW",
        "2881.TW", "2882.TW", "2891.TW", "2886.TW", "2884.TW",
        "2303.TW", "3711.TW", "2412.TW", "1301.TW", "1303.TW",
        "2002.TW", "1326.TW", "2912.TW", "5880.TW", "2357.TW",
        "3045.TW", "2395.TW", "2327.TW", "6505.TW", "2301.TW",
        "4904.TW", "9910.TW", "1101.TW", "1102.TW", "2207.TW",
        "3008.TW", "2345.TW", "5871.TW", "2609.TW", "2615.TW",
        "3034.TW", "2379.TW", "6415.TW", "3231.TW", "2049.TW",
        "8069.TW", "6669.TW", "3037.TW", "2603.TW", "2610.TW",
        "1216.TW", "2474.TW", "3443.TW", "6770.TW", "4938.TW",
    ]


# ---------------------------------------------------------------------------
# 分析與報告
# ---------------------------------------------------------------------------
def analyze_market(label: str, index_symbol: str, tickers: list) -> dict:
    """對單一市場執行完整三年回測"""
    print(f"\n{'='*60}")
    print(f"  {label} 三年歷史回測")
    print(f"{'='*60}")

    index_close = download_index(index_symbol, "3y")
    panel = download_breadth_panel(tickers, "3y")
    breadth = calc_breadth(panel)
    result = calc_exposure(index_close, breadth)

    # 統計
    total_days = len(result)
    date_range = f"{result.index[0].strftime('%Y-%m-%d')} ~ {result.index[-1].strftime('%Y-%m-%d')}"

    # 水位分佈
    exposure_counts = result["exposure"].value_counts().sort_index()
    
    # 100% 出現的時期
    full_exp = result[result["exposure"] >= 1.0]
    full_count = len(full_exp)
    full_pct = round(full_count / total_days * 100, 1)

    # 找出連續 100% 的區間
    full_periods = []
    if full_count > 0:
        is_full = (result["exposure"] >= 1.0).astype(int)
        changes = is_full.diff().fillna(0)
        starts = result.index[changes == 1]
        ends = result.index[changes == -1]
        # 處理最後一段還在 100% 的情況
        if len(starts) > len(ends):
            ends = ends.append(pd.DatetimeIndex([result.index[-1]]))
        for s, e in zip(starts, ends):
            days = len(result.loc[s:e])
            full_periods.append({"start": s.strftime("%Y-%m-%d"), "end": e.strftime("%Y-%m-%d"), "days": days})

    # 各因子的統計
    stats = {
        "trend": {
            "mean": round(result["trend"].mean(), 3),
            "pct_full": round((result["trend"] >= 1.0).mean() * 100, 1),
        },
        "breadth_raw": {
            "mean": round(result["breadth_raw"].mean() * 100, 1),
            "above_70": round((result["breadth_raw"] >= 0.70).mean() * 100, 1),
        },
        "realized_vol": {
            "mean": round(result["realized_vol"].mean() * 100, 1),
            "median": round(result["realized_vol"].median() * 100, 1),
            "below_target": round((result["realized_vol"] <= config.VOL_TARGET).mean() * 100, 1),
            "p25": round(result["realized_vol"].quantile(0.25) * 100, 1),
            "p75": round(result["realized_vol"].quantile(0.75) * 100, 1),
        },
        "vol_scale": {
            "mean": round(result["vol_scale"].mean(), 3),
            "pct_full": round((result["vol_scale"] >= 1.0).mean() * 100, 1),
        },
    }

    # 水位分佈 histogram
    bins = [0, 0.25, 0.50, 0.75, 1.0, 1.01]
    bin_labels = ["0-25%", "25-50%", "50-75%", "75-99%", "100%"]
    result["exp_bin"] = pd.cut(result["exposure"], bins=bins, labels=bin_labels, include_lowest=True)
    dist = result["exp_bin"].value_counts().sort_index()

    # 印出結果
    print(f"\n  📅 回測期間: {date_range}")
    print(f"  📊 總交易日: {total_days}")
    print(f"\n  ── 水位分佈 ──")
    for bucket, count in dist.items():
        pct = round(count / total_days * 100, 1)
        bar = "█" * int(pct / 2)
        print(f"    {bucket:>8s}: {count:>4d} 天 ({pct:>5.1f}%) {bar}")

    print(f"\n  ── 100% 水位分析 ──")
    print(f"    出現天數: {full_count} / {total_days} ({full_pct}%)")
    if full_periods:
        print(f"    出現區間:")
        for p in full_periods[:10]:  # 最多列 10 段
            print(f"      {p['start']} ~ {p['end']} ({p['days']} 天)")
    else:
        print(f"    ⚠️ 過去三年從未出現 100% 水位！")

    print(f"\n  ── 三因子拆解 ──")
    print(f"    趨勢：平均 {stats['trend']['mean']}, 滿分(1.0)佔比 {stats['trend']['pct_full']}%")
    print(f"    寬度：平均 {stats['breadth_raw']['mean']}%, ≥70%佔比 {stats['breadth_raw']['above_70']}%")
    print(f"    波動率：平均 {stats['realized_vol']['mean']}%, 中位數 {stats['realized_vol']['median']}%")
    print(f"             P25={stats['realized_vol']['p25']}%, P75={stats['realized_vol']['p75']}%")
    print(f"             ≤目標({config.VOL_TARGET*100:.0f}%)佔比: {stats['realized_vol']['below_target']}%")
    print(f"    vol_scale：平均 {stats['vol_scale']['mean']}, 滿分佔比 {stats['vol_scale']['pct_full']}%")

    # 模擬不同 VOL_TARGET 的影響
    print(f"\n  ── VOL_TARGET 敏感度分析 ──")
    for vt in [0.18, 0.20, 0.22, 0.25, 0.30]:
        sim_scale = (vt / result["realized_vol"]).clip(upper=1.0)
        sim_expo = (result["base"] * sim_scale).clip(0, 1)
        sim_expo = (sim_expo / config.EXP_STEP).round() * config.EXP_STEP
        pct_100 = round((sim_expo >= 1.0).mean() * 100, 1)
        avg_exp = round(sim_expo.mean() * 100, 1)
        print(f"    VOL_TARGET={vt*100:.0f}%: 平均水位={avg_exp}%, 100%出現={pct_100}%")

    return {
        "label": label,
        "date_range": date_range,
        "total_days": total_days,
        "full_count": full_count,
        "full_pct": full_pct,
        "full_periods": full_periods,
        "stats": stats,
        "distribution": {k: int(v) for k, v in dist.items()},
        "result_df": result,
    }


# ---------------------------------------------------------------------------
# 固定 vs 自適應 VOL_TARGET 報酬比較（--compare）
# ---------------------------------------------------------------------------
def simulate_returns(index_close: pd.Series, exposure: pd.Series) -> dict:
    """以「昨日水位 × 今日指數報酬」模擬策略淨值，回傳績效指標。"""
    ret = index_close.pct_change()
    strat = (ret * exposure.shift(1)).dropna()
    n = len(strat)
    equity = (1 + strat).cumprod()
    ann_ret = equity.iloc[-1] ** (252 / n) - 1
    ann_vol = strat.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0.0
    maxdd = float((equity / equity.cummax() - 1).min())
    return {
        "ann_ret": round(ann_ret * 100, 2),
        "ann_vol": round(ann_vol * 100, 2),
        "sharpe": round(sharpe, 2),
        "maxdd": round(maxdd * 100, 2),
        "avg_expo": round(float(exposure.reindex(strat.index).mean()) * 100, 1),
    }


def compare_market(label: str, index_symbol: str, tickers: list,
                   market: str, period: str) -> None:
    """同一市場比較：買進持有 vs 固定目標 vs 自適應目標。"""
    print(f"\n{'='*60}")
    print(f"  {label} 固定 vs 自適應 VOL_TARGET（{period}）")
    print(f"{'='*60}")

    index_close = download_index(index_symbol, period)
    panel = download_breadth_panel(tickers, period)
    breadth = calc_breadth(panel)
    realized = index_close.pct_change().rolling(20).std() * np.sqrt(252)

    fixed_target = (config.VOL_TARGET_US if market == "US" else config.VOL_TARGET_TW)
    adaptive_target = adv_market.vol_target_series(realized, market, adaptive=True)

    variants = {
        "買進持有 100%": pd.Series(1.0, index=index_close.index),
        f"固定目標 {fixed_target:.0%}": calc_exposure(index_close, breadth, fixed_target)["exposure"],
        "自適應目標(1y分位)": calc_exposure(index_close, breadth, adaptive_target)["exposure"],
    }

    print(f"\n  自適應目標範圍: {adaptive_target.min()*100:.1f}% ~ {adaptive_target.max()*100:.1f}%"
          f"（中位 {adaptive_target.median()*100:.1f}%）")
    print(f"\n  {'變體':<18s} {'年化報酬':>8s} {'年化波動':>8s} {'夏普':>6s} {'最大回撤':>8s} {'平均水位':>8s}")
    for name, expo in variants.items():
        m = simulate_returns(index_close, expo)
        print(f"  {name:<18s} {m['ann_ret']:>7.2f}% {m['ann_vol']:>7.2f}% "
              f"{m['sharpe']:>6.2f} {m['maxdd']:>7.2f}% {m['avg_expo']:>7.1f}%")


def run_compare(period: str) -> None:
    print(f"🔬 固定 vs 自適應 VOL_TARGET 報酬比較（{period}）...\n")
    compare_market("🇹🇼 台股", "^TWII", get_tw_tickers(), "TW", period)
    compare_market("🇺🇸 美股", "^GSPC", get_sp500_tickers(), "US", period)


# ---------------------------------------------------------------------------
# 主程式
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="曝險水位回測")
    parser.add_argument("--compare", action="store_true",
                        help="比較固定 vs 自適應 VOL_TARGET 的報酬")
    parser.add_argument("--period", default="3y", help="回測期間（如 3y、5y）")
    args = parser.parse_args()

    if args.compare:
        run_compare(args.period)
        return

    print("🔬 開始三年歷史曝險水位回測...\n")

    tw = analyze_market("🇹🇼 台股", "^TWII", get_tw_tickers())
    us = analyze_market("🇺🇸 美股", "^GSPC", get_sp500_tickers())

    # 寫出 JSON 報告
    report = {
        "generated_at": datetime.now().isoformat(),
        "config": {
            "VOL_TARGET": config.VOL_TARGET,
            "BREADTH_LOW": config.BREADTH_LOW,
            "BREADTH_HIGH": config.BREADTH_HIGH,
            "EXP_W_TREND": config.EXP_W_TREND,
            "EXP_W_BREADTH": config.EXP_W_BREADTH,
            "EXP_STEP": config.EXP_STEP,
        },
        "TW": {
            "date_range": tw["date_range"],
            "total_days": tw["total_days"],
            "full_count": tw["full_count"],
            "full_pct": tw["full_pct"],
            "full_periods": tw["full_periods"],
            "stats": tw["stats"],
            "distribution": tw["distribution"],
        },
        "US": {
            "date_range": us["date_range"],
            "total_days": us["total_days"],
            "full_count": us["full_count"],
            "full_pct": us["full_pct"],
            "full_periods": us["full_periods"],
            "stats": us["stats"],
            "distribution": us["distribution"],
        },
    }

    out_path = "data/results/exposure_backtest_3y.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n  [+] 報告已存為 {out_path}")

    # 總結
    print(f"\n{'='*60}")
    print(f"  📋 總結")
    print(f"{'='*60}")
    print(f"  台股 100% 水位: {tw['full_count']}天/{tw['total_days']}天 ({tw['full_pct']}%)")
    print(f"  美股 100% 水位: {us['full_count']}天/{us['total_days']}天 ({us['full_pct']}%)")
    print(f"  當前 VOL_TARGET: {config.VOL_TARGET*100:.0f}%")
    print(f"  台股波動率中位數: {tw['stats']['realized_vol']['median']}%")
    print(f"  美股波動率中位數: {us['stats']['realized_vol']['median']}%")


if __name__ == "__main__":
    main()
