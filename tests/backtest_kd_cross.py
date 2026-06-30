"""回測 KD 黃金交叉/死亡交叉 策略

分析內容：
1. 針對 ETF 觀察名單計算 9 日 KD。
2. 找出所有黃金交叉與死亡交叉事件。
3. 根據交叉發生時的 K 值分組（超賣 <20、低檔 <50、一般；超買 >80、高檔 >50、一般）。
4. 計算每次交叉後 5日、10日、20日的平均報酬率與勝率。
"""
import sys
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime

# ---------------------------------------------------------------------------
# KD 計算
# ---------------------------------------------------------------------------
def calc_kd(df: pd.DataFrame, period: int = 9) -> tuple[pd.Series, pd.Series]:
    """計算 KD 指標（9 日隨機指標）。"""
    low_min = df["Low"].rolling(period).min()
    high_max = df["High"].rolling(period).max()
    rsv = (df["Close"] - low_min) / (high_max - low_min) * 100
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    return k, d

def get_forward_returns(df: pd.DataFrame, periods: list = [5, 10, 20]) -> pd.DataFrame:
    """計算未來 N 日的報酬率"""
    returns = pd.DataFrame(index=df.index)
    for p in periods:
        returns[f"fwd_{p}d"] = df["Close"].shift(-p) / df["Close"] - 1
    return returns

# ---------------------------------------------------------------------------
# 主程式
# ---------------------------------------------------------------------------
def main():
    WATCHLIST = {"0050.TW": "元大台灣50", "VOO": "S&P 500", "SMH": "半導體 (SMH)", "SOXQ": "半導體 (SOXQ)"}
    
    print("🔬 開始回測 KD 交叉訊號 (歷史 5 年數據)...\n")
    
    results_gc = []
    results_dc = []
    
    for code, name in WATCHLIST.items():
        print(f"  [>] 下載與分析 {code} ({name})...")
        df = yf.download(code, period="5y", interval="1d", auto_adjust=True, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if len(df) < 50:
            print(f"      資料不足，跳過。")
            continue
            
        k, d = calc_kd(df)
        fwd_returns = get_forward_returns(df)
        
        # 尋找交叉
        prev_k = k.shift(1)
        prev_d = d.shift(1)
        
        # 黃金交叉：前一日 K <= D，當日 K > D
        is_gc = (prev_k <= prev_d) & (k > d)
        # 死亡交叉：前一日 K >= D，當日 K < D
        is_dc = (prev_k >= prev_d) & (k < d)
        
        for idx in df.index[is_gc]:
            curr_k = k.loc[idx]
            zone = "超賣 <20" if curr_k < 20 else ("低檔 <50" if curr_k < 50 else "一般 >=50")
            ret = fwd_returns.loc[idx]
            results_gc.append({
                "code": code,
                "date": idx,
                "k": curr_k,
                "zone": zone,
                "fwd_5d": ret["fwd_5d"],
                "fwd_10d": ret["fwd_10d"],
                "fwd_20d": ret["fwd_20d"]
            })
            
        for idx in df.index[is_dc]:
            curr_k = k.loc[idx]
            zone = "超買 >80" if curr_k > 80 else ("高檔 >50" if curr_k > 50 else "一般 <=50")
            ret = fwd_returns.loc[idx]
            results_dc.append({
                "code": code,
                "date": idx,
                "k": curr_k,
                "zone": zone,
                "fwd_5d": ret["fwd_5d"],
                "fwd_10d": ret["fwd_10d"],
                "fwd_20d": ret["fwd_20d"]
            })

    df_gc = pd.DataFrame(results_gc).dropna()
    df_dc = pd.DataFrame(results_dc).dropna()
    
    print(f"\n{'='*60}")
    print(f"  📈 黃金交叉 (買進訊號) 回測結果")
    print(f"{'='*60}")
    
    # 依區間分組統計黃金交叉
    for zone in ["超賣 <20", "低檔 <50", "一般 >=50"]:
        subset = df_gc[df_gc["zone"] == zone]
        count = len(subset)
        if count == 0: continue
        print(f"\n  [區間: {zone}] 樣本數: {count} 次")
        for p in [5, 10, 20]:
            col = f"fwd_{p}d"
            avg_ret = subset[col].mean() * 100
            win_rate = (subset[col] > 0).mean() * 100
            print(f"    後 {p:2d} 日: 平均報酬 {avg_ret:>5.2f}% | 勝率 {win_rate:>5.1f}%")

    print(f"\n{'='*60}")
    print(f"  📉 死亡交叉 (賣出訊號) 回測結果")
    print(f"{'='*60}")
    
    # 依區間分組統計死亡交叉
    for zone in ["超買 >80", "高檔 >50", "一般 <=50"]:
        subset = df_dc[df_dc["zone"] == zone]
        count = len(subset)
        if count == 0: continue
        print(f"\n  [區間: {zone}] 樣本數: {count} 次")
        for p in [5, 10, 20]:
            col = f"fwd_{p}d"
            avg_ret = subset[col].mean() * 100
            win_rate = (subset[col] > 0).mean() * 100
            # 死亡交叉代表可能下跌，所以勝率看跌的比例
            drop_rate = (subset[col] < 0).mean() * 100
            print(f"    後 {p:2d} 日: 平均報酬 {avg_ret:>5.2f}% | 續跌機率 {drop_rate:>5.1f}%")

if __name__ == "__main__":
    main()
