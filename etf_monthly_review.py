"""ETF 每月警報策略自動驗證機制。

此腳本設計於每日排程中呼叫，但只會在每個月的第一個「交易日（平日）」真正執行。
它會回測核心與衛星 ETF 過去 3 年的 KD 黃金交叉勝率，並確保有足夠樣本數才判定有效。
"""
import os
import sys
import json
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# 設定區
# ---------------------------------------------------------------------------
WATCHLIST = {
    "VOO": "🇺🇸 VOO (S&P 500)",
    "SMH": "🇺🇸 SMH (半導體)",
    "SOXQ": "🇺🇸 SOXQ (半導體)",
    "0050.TW": "🇹🇼 0050 (台灣50)",
    "2330.TW": "🇹🇼 2330 (台積電)",
    "00981A.TW": "🇹🇼 00981A (統一增長)"
}

def is_first_weekday_of_month() -> bool:
    """判斷今天是否為該月第一個平日 (週一到週五)。"""
    today = datetime.now().date()
    # 如果今天是 1 號，且是週一到週五，那就是第一天
    if today.day == 1 and today.weekday() < 5:
        return True
    # 如果今天是 2 號或 3 號，且剛好是週一 (代表 1 號是週末)
    if today.weekday() == 0 and today.day <= 3:
        return True
    return False

# ---------------------------------------------------------------------------
# KD 與回測邏輯
# ---------------------------------------------------------------------------
def calc_kd(df: pd.DataFrame, period: int = 9) -> tuple[pd.Series, pd.Series]:
    low_min = df["Low"].rolling(period).min()
    high_max = df["High"].rolling(period).max()
    rsv = (df["Close"] - low_min) / (high_max - low_min) * 100
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    return k, d

def run_backtest() -> str:
    """執行回測並組合推播訊息。"""
    lines = [
        "【🔍 每月 ETF 警報策略體檢】",
        "回測期間：近 3 年",
        "驗證策略：低檔 KD 20-50 黃金交叉",
        "(以進場後 20 日勝率為準)\n"
    ]
    
    for code, name in WATCHLIST.items():
        df = yf.download(code, period="3y", interval="1d", auto_adjust=True, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        if len(df) < 20:
            lines.append(f"{name}\n⏳ 剛上市，無足夠資料\n")
            continue
            
        k, d = calc_kd(df)
        fwd_20d = df["Close"].shift(-20) / df["Close"] - 1
        
        prev_k, prev_d = k.shift(1), d.shift(1)
        # 條件：昨天 K<=D，今天 K>D，且發生在 KD 20~50 之間
        is_gc = (prev_k <= prev_d) & (k > d)
        condition = (k >= 20) & (k < 50)
        
        events = df.index[is_gc & condition]
        rets = fwd_20d.loc[events].dropna()
        count = len(rets)
        
        if count < 4:
            lines.append(f"{name}\n⏳ 樣本不足 ({count}次)，持續觀察\n")
        else:
            win_rate = (rets > 0).mean() * 100
            avg_ret = rets.mean() * 100
            if win_rate < 50:
                lines.append(f"{name}\n🚨 策略衰退 (勝率 {win_rate:.1f}% | {count}次)\n")
            else:
                lines.append(f"{name}\n✅ 策略健康 (勝率 {win_rate:.1f}% | {count}次)\n")
                
    return "\n".join(lines).strip()

# ---------------------------------------------------------------------------
# LINE 推播
# ---------------------------------------------------------------------------
def send_line_multicast(message: str) -> None:
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    if not token:
        print("[!] LINE_CHANNEL_ACCESS_TOKEN 未設定")
        return
        
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
    
    allowed_ids_str = os.getenv("LINE_ALLOWED_USER_IDS", "")
    allowed_ids = [uid.strip() for uid in allowed_ids_str.split(",") if uid.strip()]
    
    if allowed_ids:
        data = {"to": allowed_ids, "messages": [{"type": "text", "text": message}]}
        url = "https://api.line.me/v2/bot/message/multicast"
    else:
        data = {"messages": [{"type": "text", "text": message}]}
        url = "https://api.line.me/v2/bot/message/broadcast"
        
    resp = requests.post(url, headers=headers, json=data)
    if resp.status_code != 200:
        print(f"[!] LINE 推播失敗: {resp.text}")
    else:
        print("[+] 成功推播每月體檢報告。")

# ---------------------------------------------------------------------------
# 主程式
# ---------------------------------------------------------------------------
def main():
    force_run = "--force" in sys.argv
    if not force_run and not is_first_weekday_of_month():
        # 今天不是發送日，安靜退出
        sys.exit(0)
        
    print("開始執行 ETF 每月策略體檢...")
    report_msg = run_backtest()
    print("\n" + report_msg + "\n")
    send_line_multicast(report_msg)

if __name__ == "__main__":
    main()
