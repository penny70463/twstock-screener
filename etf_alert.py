"""每日 ETF 與大盤進出場 LINE 自動警報系統

邏輯：每天在 run_pipeline.py 跑完後執行，比對前一日的狀態（alert_state.json），
只有在「大盤曝險水位」或「ETF 燈號」發生有意義的變化時，才會推播 LINE。

用法：
    python etf_alert.py              # 正常執行（含 LINE 推播）
    python etf_alert.py --dry-run    # 只印出結果，不發送 LINE
"""
import os
import json
import argparse
import requests
from pathlib import Path
from dotenv import load_dotenv

# 載入環境變數
load_dotenv()

RESULT_DIR = Path("data/results")
STATE_FILE = RESULT_DIR / "alert_state.json"

# 曝險水位變化門檻：變化量（絕對值）超過此值才觸發警報，避免微幅波動每天通知
EXPOSURE_CHANGE_THRESHOLD = 0.10  # 10%


# ---------------------------------------------------------------------------
# LINE 推播
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# 大盤曝險水位檢查
# ---------------------------------------------------------------------------
def _check_exposure(market_label: str, market_key: str, market_state: dict,
                    old_state: dict, new_state: dict, alerts: list) -> None:
    """檢查單一市場的曝險水位是否發生有意義的變化。"""
    if "exposure" not in market_state:
        return

    curr_exp = market_state["exposure"]
    new_state["exposure"][market_key] = curr_exp

    old_exp = old_state.get("exposure", {}).get(market_key)
    if old_exp is None:
        print(f"  [i] {market_label}：首次記錄曝險水位 {int(curr_exp*100)}%，建立基準。")
        return

    change = curr_exp - old_exp
    vol = market_state.get("realized_vol", "N/A")

    # 水位下降超過門檻 → 風險警報
    if change <= -EXPOSURE_CHANGE_THRESHOLD:
        alerts.append(
            f"⚠️【{market_label}風險警報】"
            f"波動率升至 {vol}%，三因子建議持股水位從 {int(old_exp*100)}% 降至 {int(curr_exp*100)}%！"
            f"請優先停利波動大的部位換取現金。"
        )
    # 水位重新回到滿水位（不論變化量大小）→ 警報解除
    elif curr_exp >= 1.0 and old_exp < 1.0:
        alerts.append(
            f"✅【{market_label}警報解除】"
            f"市場波動回穩，建議水位從 {int(old_exp*100)}% 恢復至 100%！"
            f"手邊現金可重新投入核心 ETF。"
        )
    # 尚未滿水位，但大幅好轉 → 風險緩解（可分批買回）
    elif curr_exp < 1.0 and change >= EXPOSURE_CHANGE_THRESHOLD:
        alerts.append(
            f"📈【{market_label}風險緩解】"
            f"建議水位從 {int(old_exp*100)}% 回升至 {int(curr_exp*100)}%，"
            f"波動率降至 {vol}%，可考慮適度分批買回。"
        )


# ---------------------------------------------------------------------------
# ETF 燈號檢查
# ---------------------------------------------------------------------------
# 定義所有有意義的狀態轉換及其對應的文案
SIGNAL_TRANSITIONS = {
    ("green", "yellow"): {
        "emoji": "🟡",
        "title": "逢低佈局提醒",
        "desc": lambda code, name, price, ma50, ma200: (
            f"{code} ({name}) 跌破 50 日均線，進入長線回檔買點區！"
            f"最新價 ${price} (季線壓力 ${ma50})"
        ),
    },
    ("yellow", "green"): {
        "emoji": "🟢",
        "title": "轉強加碼提醒",
        "desc": lambda code, name, price, ma50, ma200: (
            f"{code} ({name}) 洗盤結束，重新站上 50 日均線！"
            f"最新價 ${price} (防守價 ${ma50})"
        ),
    },
    ("red", "yellow"): {
        "emoji": "🟡",
        "title": "初步止穩提醒",
        "desc": lambda code, name, price, ma50, ma200: (
            f"{code} ({name}) 重新站回 200 日均線，空頭趨勢初步解除！"
            f"最新價 ${price} (仍需站穩 50 日均線 ${ma50} 才確認多頭)"
        ),
    },
    ("red", "green"): {
        "emoji": "🟢",
        "title": "多頭確認提醒",
        "desc": lambda code, name, price, ma50, ma200: (
            f"{code} ({name}) 直接從空頭翻多，站上 50 日與 200 日均線！"
            f"最新價 ${price} (防守價 ${ma50})"
        ),
    },
    ("green", "red"): {
        "emoji": "🔴",
        "title": "破線警報",
        "desc": lambda code, name, price, ma50, ma200: (
            f"{code} ({name}) 急跌破 200 日長期均線，長線趨勢轉空！"
            f"最新價 ${price} (請嚴格執行停損)"
        ),
    },
    ("yellow", "red"): {
        "emoji": "🔴",
        "title": "破線警報",
        "desc": lambda code, name, price, ma50, ma200: (
            f"{code} ({name}) 跌破 200 日長期均線，長線趨勢轉空！"
            f"最新價 ${price} (請嚴格執行停損)"
        ),
    },
}


def _check_etf_signals(old_state: dict, new_state: dict, alerts: list) -> None:
    """檢查所有 ETF 的燈號是否發生變化。"""
    latest_etf_file = RESULT_DIR / "latest_etf.json"
    if not latest_etf_file.exists():
        print("  [!] latest_etf.json 不存在，跳過 ETF 檢查。")
        return

    etf_data = json.loads(latest_etf_file.read_text(encoding="utf-8"))
    WATCHLIST = ["0050.TW", "VOO", "SMH", "SOXQ"]

    for etf in etf_data.get("etfs", []):
        code = etf.get("code")
        if code not in WATCHLIST:
            continue

        curr_signal = etf.get("signal")
        new_state["etfs"][code] = curr_signal

        old_signal = old_state.get("etfs", {}).get(code)

        if old_signal is None:
            print(f"  [i] {code}：首次記錄燈號 [{curr_signal}]，建立基準。")
            continue

        if old_signal == curr_signal:
            continue

        transition = SIGNAL_TRANSITIONS.get((old_signal, curr_signal))
        if transition:
            name = etf.get("name")
            price = etf.get("price")
            ma50 = etf.get("ma50")
            ma200 = etf.get("ma200")
            desc_text = transition["desc"](code, name, price, ma50, ma200)
            alerts.append(f"{transition['emoji']}【{transition['title']}】{desc_text}")
        else:
            print(f"  [?] {code}：未定義的狀態轉換 {old_signal} → {curr_signal}")


# ---------------------------------------------------------------------------
# 主邏輯
# ---------------------------------------------------------------------------
def check_and_alert(dry_run: bool = False):
    # 載入過去狀態
    old_state = {}
    is_first_run = not STATE_FILE.exists()
    if not is_first_run:
        old_state = json.loads(STATE_FILE.read_text(encoding="utf-8"))

    if is_first_run:
        print("  [i] 首次執行警報系統，本次僅建立基準狀態，不發送通知。")

    # 準備新狀態
    new_state = {"etfs": {}, "exposure": {}}
    alerts = []

    # 1. 檢查台股大盤曝險水位
    latest_tw_file = RESULT_DIR / "latest_tw.json"
    if latest_tw_file.exists():
        tw_data = json.loads(latest_tw_file.read_text(encoding="utf-8"))
        tw_market = tw_data.get("market_state", {})
        _check_exposure("🇹🇼 台股", "TW", tw_market, old_state, new_state, alerts)

    # 2. 檢查美股大盤曝險水位
    latest_us_file = RESULT_DIR / "latest_us.json"
    if latest_us_file.exists():
        us_data = json.loads(latest_us_file.read_text(encoding="utf-8"))
        us_market = us_data.get("market_state", {})
        _check_exposure("🇺🇸 美股", "US", us_market, old_state, new_state, alerts)

    # 3. 檢查 ETF 訊號
    _check_etf_signals(old_state, new_state, alerts)

    # 推播
    if alerts:
        msg = "\n\n".join(alerts)
        print("  [!] 觸發警報條件：")
        print(msg)
        if not dry_run:
            send_line_message(msg)
        else:
            print("  [i] --dry-run 模式，跳過 LINE 推播。")
    else:
        print("  [i] 狀態無變化，無須推播。")

    # 寫入新的狀態（不論是否有警報，都要更新基準）
    STATE_FILE.write_text(json.dumps(new_state, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  [+] 狀態已寫入 {STATE_FILE}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="每日 ETF 與大盤進出場警報")
    parser.add_argument("--dry-run", action="store_true", help="只印出結果，不發送 LINE")
    args = parser.parse_args()
    check_and_alert(dry_run=args.dry_run)
