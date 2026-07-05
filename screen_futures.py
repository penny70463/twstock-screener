# -*- coding: utf-8 -*-
"""台指期波段策略（美股信號 + 法人籌碼）

策略邏輯：
- 美股開盤漲跌 (AAPL/QQQ) → 台股開盤前預判
- 期交所法人籌碼 (投信多空) → 進出場信號
- 台指期目標點位 = 今日加權指數 ± alpha

回測結論（待補充）

用法：
    python screen_futures.py [YYYY-MM-DD]

輸出：data/results/futures_tw.json + CSV
相依：pandas yfinance src.market_regime src.advisor.margin_futures
"""
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

from src.market_regime import get_regime
from src.advisor.margin_futures import fetch_futures_cached

REPO = Path(__file__).resolve().parent
ASOF = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") \
    else dt.date.today().isoformat()
OUT_JSON = REPO / "data" / "results" / "futures_tw.json"

# 台指期參數
US_OPEN_LOOKBACK = 5  # 美股開盤前 5 日漲跌判定
TRUST_WEIGHT = 0.6    # 投信籌碼權重
DEALER_WEIGHT = 0.3   # 自營商權重
FOREIGN_WEIGHT = 0.1  # 外資權重


def get_us_overnight_signal() -> dict:
    """美股隔夜信號（開盤前判定）

    Returns:
        {
            "us_signal": "bullish" | "bearish" | "neutral",
            "aapl_change": ...,
            "qqq_change": ...,
            "sp500_change": ...,
        }
    """
    try:
        # 下載最近 5 個交易日的美股數據
        aapl = yf.download("AAPL", period="1w", progress=False)["Close"]
        qqq = yf.download("QQQ", period="1w", progress=False)["Close"]
        sp500 = yf.download("^GSPC", period="1w", progress=False)["Close"]

        if len(aapl) < 2:
            return {"us_signal": "neutral", "note": "美股數據不足"}

        aapl_chg = (aapl.iloc[-1] / aapl.iloc[0] - 1) * 100
        qqq_chg = (qqq.iloc[-1] / qqq.iloc[0] - 1) * 100
        sp500_chg = (sp500.iloc[-1] / sp500.iloc[0] - 1) * 100

        # 簡單投票制
        bullish_count = sum([
            aapl_chg > 0,
            qqq_chg > 0,
            sp500_chg > 0,
        ])

        signal = "bullish" if bullish_count >= 2 else "bearish" if bullish_count == 0 else "neutral"

        return {
            "us_signal": signal,
            "aapl_change_pct": round(aapl_chg, 2),
            "qqq_change_pct": round(qqq_chg, 2),
            "sp500_change_pct": round(sp500_chg, 2),
        }
    except Exception as e:
        print(f"[-] 美股信號抓取失敗: {e}")
        return {"us_signal": "unknown", "note": str(e)}


def get_taifex_signal(day: dt.date) -> dict:
    """期交所法人籌碼信號

    Returns:
        {
            "taifex_signal": "bullish" | "bearish" | "unknown",
            "trust_net": ...,  # 投信 (多 - 空)
            "dealer_net": ...,
            "foreign_net": ...,
        }
    """
    try:
        # 台指期
        data_txf = fetch_futures_cached(day, product="TXF")
        if data_txf is None:
            return {"taifex_signal": "unknown", "note": "期貨籌碼無法取得"}

        # 簡化版：假設 data 中包含 trust_buy/sell 等字段
        # 實際格式需根據期交所 API 調整
        futures_data = data_txf.get("data", {})

        # 計算淨多單 (假設 API 回傳格式)
        trust_net = (
            int(futures_data.get("trust_long", 0)) -
            int(futures_data.get("trust_short", 0))
        )
        dealer_net = (
            int(futures_data.get("dealer_long", 0)) -
            int(futures_data.get("dealer_short", 0))
        )
        foreign_net = (
            int(futures_data.get("foreign_long", 0)) -
            int(futures_data.get("foreign_short", 0))
        )

        # 加權信號
        net_score = (
            trust_net * TRUST_WEIGHT +
            dealer_net * DEALER_WEIGHT +
            foreign_net * FOREIGN_WEIGHT
        )

        signal = "bullish" if net_score > 0 else "bearish" if net_score < 0 else "neutral"

        return {
            "taifex_signal": signal,
            "trust_net": int(trust_net),
            "dealer_net": int(dealer_net),
            "foreign_net": int(foreign_net),
            "weighted_score": round(net_score, 0),
        }
    except Exception as e:
        print(f"[-] 期交所籌碼抓取失敗: {e}")
        return {"taifex_signal": "unknown", "note": str(e)}


def main() -> None:
    asof = dt.date.fromisoformat(ASOF)
    regime = get_regime(asof)

    # 美股信號
    us_sig = get_us_overnight_signal()

    # 期交所籌碼
    taifex_sig = get_taifex_signal(asof)

    # 下載加權指數（作為台指期基準）
    try:
        twii = yf.download("^TWII", period="1d", progress=False)
        twii_close = float(twii["Close"].iloc[-1])
    except Exception:
        twii_close = None

    # 合成進出場信號
    us_bullish = us_sig.get("us_signal") == "bullish"
    taifex_bullish = taifex_sig.get("taifex_signal") == "bullish"

    # 簡單投票
    buy_signal = us_bullish and taifex_bullish
    sell_signal = (not us_bullish) and (not taifex_bullish)
    mixed_signal = not (buy_signal or sell_signal)

    payload = {
        "date": ASOF,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "regime": regime,
        "twii_close": round(twii_close, 2) if twii_close else None,
        "us_signal": us_sig,
        "taifex_signal": taifex_sig,
        "params": {
            "product": "TXF",  # 台指期
            "trust_weight": TRUST_WEIGHT,
            "dealer_weight": DEALER_WEIGHT,
            "foreign_weight": FOREIGN_WEIGHT,
        },
        "signals": {
            "buy_signal": buy_signal,
            "sell_signal": sell_signal,
            "mixed_signal": mixed_signal,
        },
        "entry_exit": None,
    }

    # 根據信號計算進出場點位
    if twii_close:
        if buy_signal:
            # 多頭：目標上漲 1% (保守版本，回測後調整)
            target_up = round(twii_close * 1.01)
            target_down = round(twii_close * 0.99)
            payload["entry_exit"] = {
                "signal": "買進訊號",
                "entry": round(twii_close * 0.995),  # 跌破支撐再進場
                "target": target_up,
                "stop_loss": target_down,
            }
        elif sell_signal:
            # 空頭：目標下跌 1%
            target_down = round(twii_close * 0.99)
            target_up = round(twii_close * 1.01)
            payload["entry_exit"] = {
                "signal": "賣出訊號",
                "entry": round(twii_close * 1.005),  # 漲破壓力再出場
                "target": target_down,
                "stop_loss": target_up,
            }
        else:
            payload["entry_exit"] = {
                "signal": "持平觀望",
                "note": "美股與期貨籌碼訊號不一致，暫不建倉",
            }

    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"台指期策略信號生成完成 → {OUT_JSON}")


if __name__ == "__main__":
    main()
