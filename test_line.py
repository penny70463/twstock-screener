import json
import pandas as pd
from src.pipeline import _send_line_broadcast
from config import RESULT_DIR
from dotenv import load_dotenv

load_dotenv()

def test_push(market):
    print(f"測試推播 {market} 市場...")
    file_path = RESULT_DIR / f"latest_{market.lower()}.json"
    if not file_path.exists():
        print(f"找不到 {file_path}")
        return
        
    with open(file_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
        
    df = pd.DataFrame(payload.get("screened", []))
    _send_line_broadcast(payload, df, market=market)

if __name__ == "__main__":
    test_push("TW")
    test_push("US")
