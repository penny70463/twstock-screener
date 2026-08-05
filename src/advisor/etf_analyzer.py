"""ETF 存股紅綠燈分析模組

負責抓取核心 ETF 的歷史報價，計算 50MA 與 200MA，
並依據收盤價與均線的相對位置，判定紅綠燈狀態與進出場參考價。
"""
import pandas as pd
from datetime import datetime
from src.advisor import data, indicators

ETF_LIST = {
    # Core (大盤核心)
    "0050.TW": "0050.TW",
    "0056.TW": "0056.TW",
    "VOO": "VOO",
    "QQQ": "QQQ",
    "2330.TW": "2330.TW",
    # Satellite (衛星/產業波段)
    "SMH": "SMH",
    "SOXQ": "SOXQ",
    "00981A.TW": "00981A.TW",
    "XLK": "XLK",
    "XLF": "XLF",
    "XLC": "XLC",
    "XLV": "XLV",
    "XLE": "XLE",
    "XLY": "XLY",
    "XLP": "XLP",
    "XLI": "XLI",
    "XLU": "XLU",
    "XLRE": "XLRE",
    "XLB": "XLB"
}

ETF_NAMES = {
    "0050.TW": "元大台灣50",
    "0056.TW": "元大高股息",
    "VOO": "S&P 500",
    "QQQ": "Nasdaq 100",
    "SMH": "半導體 (SMH)",
    "SOXQ": "半導體 (SOXQ)",
    "2330.TW": "台積電",
    "00981A.TW": "統一增長",
    "XLK": "科技 (XLK)",
    "XLF": "金融 (XLF)",
    "XLC": "通訊 (XLC)",
    "XLV": "醫療 (XLV)",
    "XLE": "能源 (XLE)",
    "XLY": "非必須消費 (XLY)",
    "XLP": "必需消費 (XLP)",
    "XLI": "工業 (XLI)",
    "XLU": "公用事業 (XLU)",
    "XLRE": "房地產 (XLRE)",
    "XLB": "原物料 (XLB)"
}

CORE_ETFS = {"0050.TW", "0056.TW", "VOO", "QQQ", "2330.TW"}

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
        
        is_core = code in CORE_ETFS
        
        # 判定紅綠燈狀態
        if current_price > current_ma50 and current_price > current_ma200:
            signal = "green"
            desc = "多頭續航" if is_core else "波段強勢"
        elif current_price > current_ma200 and current_price <= current_ma50:
            signal = "yellow"
            desc = "回檔買點" if is_core else "波段轉弱"
        else:
            signal = "red"
            desc = "空頭觀望" if is_core else "趨勢破壞"
            
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
