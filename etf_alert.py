import os
import json
import requests
from pathlib import Path
from dotenv import load_dotenv

# 載入環境變數
load_dotenv()

RESULT_DIR = Path("data/results")
STATE_FILE = RESULT_DIR / "alert_state.json"

def send_line_message(message: str) -> None:
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    if not token:
        print("  [!] LINE_CHANNEL_ACCESS_TOKEN 未設定，無法推播。")
        return
        
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    
    allowed_ids_str = os.getenv("LINE_ALLOWED_USER_IDS", "")
    allowed_ids = [uid.strip() for uid in allowed_ids_str.split(",") if uid.strip()]

    if allowed_ids:
        data = {"to": allowed_ids, "messages": [{"type": "text", "text": message}]}
        api_url = "https://api.line.me/v2/bot/message/multicast"
    else:
        data = {"messages": [{"type": "text", "text": message}]}
        api_url = "https://api.line.me/v2/bot/message/broadcast"

    try:
        res = requests.post(api_url, headers=headers, json=data, timeout=10)
        if res.status_code == 200:
            print("  [+] LINE 進場警報推播發送成功！", flush=True)
        else:
            print(f"  [-] LINE 推播失敗: {res.text}", flush=True)
    except Exception as e:
        print(f"  [-] LINE 推播發生例外錯誤: {e}", flush=True)

def check_and_alert():
    # 載入過去狀態
    old_state = {}
    if STATE_FILE.exists():
        old_state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        
    # 準備新狀態
    new_state = {"etfs": {}, "exposure": {}}
    alerts = []
    
    # 1. 檢查大盤曝險水位 (美股為主，因為這跟海外 ETF 比較相關)
    latest_us_file = RESULT_DIR / "latest_us.json"
    if latest_us_file.exists():
        us_data = json.loads(latest_us_file.read_text(encoding="utf-8"))
        market_state = us_data.get("market_state", {})
        if "exposure" in market_state:
            curr_exp = market_state["exposure"]
            new_state["exposure"]["US"] = curr_exp
            
            old_exp = old_state.get("exposure", {}).get("US")
            
            if old_exp is not None:
                if curr_exp < old_exp:
                    alerts.append(f"⚠️【大盤風險警報】美股波動率飆高，三因子建議持股水位降至 {int(curr_exp*100)}%！請優先停利 SMH/SOXQ 等波動大的部位換取現金。")
                elif curr_exp > old_exp and curr_exp >= 1.0:
                    alerts.append(f"✅【大盤警報解除】市場波動回穩，建議水位恢復至 100%！手邊現金可重新投入核心 ETF。")
                    
    # 2. 檢查 ETF 訊號
    latest_etf_file = RESULT_DIR / "latest_etf.json"
    if latest_etf_file.exists():
        etf_data = json.loads(latest_etf_file.read_text(encoding="utf-8"))
        WATCHLIST = ["0050.TW", "VOO", "SMH", "SOXQ"]
        
        for etf in etf_data.get("etfs", []):
            code = etf.get("code")
            if code not in WATCHLIST:
                continue
                
            curr_signal = etf.get("signal")
            new_state["etfs"][code] = curr_signal
            
            old_signal = old_state.get("etfs", {}).get(code)
            
            if old_signal and old_signal != curr_signal:
                name = etf.get("name")
                price = etf.get("price")
                ma50 = etf.get("ma50")
                
                if curr_signal == "yellow" and old_signal == "green":
                    alerts.append(f"🟡【逢低佈局提醒】{code} ({name}) 跌破 50 日均線，進入長線回檔買點區！最新價 ${price} (季線壓力 ${ma50})")
                elif curr_signal == "green" and old_signal == "yellow":
                    alerts.append(f"🟢【轉強加碼提醒】{code} ({name}) 洗盤結束，重新站上 50 日均線，動能轉強！最新價 ${price} (防守價 ${ma50})")
                elif curr_signal == "red":
                    alerts.append(f"🔴【破線警報】{code} ({name}) 跌破 200 日長期均線，長線趨勢轉空！最新價 ${price} (請嚴格執行停損)")
                    
    if alerts:
        msg = "\n\n".join(alerts)
        print("  [!] 觸發警報條件：")
        print(msg)
        send_line_message(msg)
    else:
        print("  [i] 狀態無變化，無須推播。")
        
    STATE_FILE.write_text(json.dumps(new_state, indent=2, ensure_ascii=False), encoding="utf-8")

if __name__ == "__main__":
    check_and_alert()
