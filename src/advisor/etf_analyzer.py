"""ETF 存股紅綠燈分析模組

負責抓取核心 ETF 的歷史報價，計算 50MA 與 200MA，
並依據收盤價與均線的相對位置，判定紅綠燈狀態與進出場參考價。
"""
import pandas as pd
from datetime import datetime
from src.advisor import data, indicators

ETF_LIST = {
    "0050.TW": "0050.TW",
    "0056.TW": "0056.TW",
    "VOO": "VOO",
    "QQQ": "QQQ",
    "SMH": "SMH",
    "SOXQ": "SOXQ"
}

ETF_NAMES = {
    "0050.TW": "元大台灣50",
    "0056.TW": "元大高股息",
    "VOO": "S&P 500",
    "QQQ": "Nasdaq 100",
    "SMH": "半導體 (SMH)",
    "SOXQ": "半導體 (SOXQ)"
}

def analyze_etfs() -> list[dict]:
    """分析核心 ETF 並回傳包含燈號狀態的資料列表"""
    # 下載歷史股價 (需要至少 200 天，所以抓 2y)
    histories = data.fetch_history(ETF_LIST, period="2y")
    
    results = []
    
    for code, df in histories.items():
        if len(df) < 200:
            continue
            
        close_prices = df["Close"]
        ma50 = indicators.sma(close_prices, 50)
        ma200 = indicators.sma(close_prices, 200)
        
        current_price = close_prices.iloc[-1]
        current_ma50 = ma50.iloc[-1]
        current_ma200 = ma200.iloc[-1]
        
        # 判定紅綠燈狀態
        if current_price > current_ma50 and current_price > current_ma200:
            signal = "green"   # 多頭續航
            desc = "多頭續航"
        elif current_price > current_ma200 and current_price <= current_ma50:
            signal = "yellow"  # 回檔買點
            desc = "回檔買點"
        else:
            signal = "red"     # 空頭觀望
            desc = "空頭觀望"
            
        results.append({
            "code": code,
            "name": ETF_NAMES.get(code, code),
            "price": round(current_price, 2),
            "ma50": round(current_ma50, 2),
            "ma200": round(current_ma200, 2),
            "signal": signal,
            "desc": desc,
            "entry_price": round(current_ma50, 2),
            "exit_price": round(current_ma200, 2)
        })
        
    # 保證輸出的順序與設定的一致
    ordered_results = []
    for code in ETF_LIST.keys():
        for r in results:
            if r["code"] == code:
                ordered_results.append(r)
                break
                
    return ordered_results
