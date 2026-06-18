import json
import pandas as pd
from src.pipeline import send_combined_line_broadcast
from config import RESULT_DIR
from dotenv import load_dotenv

load_dotenv()

def test_combined_push():
    print("測試合併推播...")
    payloads = {}
    for market in ["TW", "US"]:
        file_path = RESULT_DIR / f"latest_{market.lower()}.json"
        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                payloads[market] = json.load(f)
        
    send_combined_line_broadcast(payloads)

if __name__ == "__main__":
    test_combined_push()
