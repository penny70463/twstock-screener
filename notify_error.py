import os
import sys
from datetime import datetime
import requests
from dotenv import load_dotenv

def main():
    load_dotenv()
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    if not token:
        print("No LINE_CHANNEL_ACCESS_TOKEN found in .env")
        sys.exit(1)

    target_user_id = "U6a691e4a5f95264a3260f7ff5f0f2819"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = (
        f"🚨 警告：台美股掃描排程執行失敗！\n"
        f"發生時間：{now_str}\n"
        f"請打開 Mac 檢查 `cron_log.txt` 了解詳細錯誤原因。"
    )

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
    data = {
        "to": target_user_id,
        "messages": [{"type": "text", "text": msg}]
    }

    try:
        res = requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=data, timeout=10)
        res.raise_for_status()
        print("Error notification sent to LINE.")
    except Exception as e:
        print(f"Failed to send error notification: {e}")
        if 'res' in locals():
            print(res.text)

if __name__ == "__main__":
    main()
