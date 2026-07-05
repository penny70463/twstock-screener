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
        # 下載最近 5 個交易日的美股數據（各自下載以避免 MultiIndex）
        aapl_data = yf.download("AAPL", period="5d", progress=False)
        qqq_data = yf.download("QQQ", period="5d", progress=False)
        sp500_data = yf.download("^GSPC", period="5d", progress=False)

        # 處理 MultiIndex 列（yfinance 多股票下載時會產生）
        if isinstance(aapl_data.columns, pd.MultiIndex):
            aapl = aapl_data[("Close", "AAPL")]
        else:
            aapl = aapl_data["Close"]

        if isinstance(qqq_data.columns, pd.MultiIndex):
            qqq = qqq_data[("Close", "QQQ")]
        else:
            qqq = qqq_data["Close"]

        if isinstance(sp500_data.columns, pd.MultiIndex):
            sp500 = sp500_data[("Close", "^GSPC")]
        else:
            sp500 = sp500_data["Close"]

        if len(aapl) < 2 or len(qqq) < 2 or len(sp500) < 2:
            return {"us_signal": "neutral", "note": "美股數據不足"}

        aapl_chg = float((aapl.iloc[-1] / aapl.iloc[0] - 1) * 100)
        qqq_chg = float((qqq.iloc[-1] / qqq.iloc[0] - 1) * 100)
        sp500_chg = float((sp500.iloc[-1] / sp500.iloc[0] - 1) * 100)

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
    """期交所法人籌碼信號（優先期貨，備選現貨法人買賣超）

    Returns:
        {
            "taifex_signal": "bullish" | "bearish" | "neutral",
            "trust_net": ...,
            "dealer_net": ...,
            "foreign_net": ...,
            "weighted_score": ...,
            "source": "taifex_api" | "institutional_proxy" | "fallback",
        }
    """
    try:
        # 優先嘗試期交所期貨籌碼
        data_txf = fetch_futures_cached(day, product="TXF")
        if data_txf is not None and data_txf.get("source") == "taifex_api":
            trust_net = int(data_txf.get("trust_net", 0))
            dealer_net = int(data_txf.get("dealer_net", 0))
            foreign_net = int(data_txf.get("foreign_net", 0))

            net_score = (
                trust_net * TRUST_WEIGHT +
                dealer_net * DEALER_WEIGHT +
                foreign_net * FOREIGN_WEIGHT
            )
            signal = "bullish" if net_score > 0 else "bearish" if net_score < 0 else "neutral"
            return {
                "taifex_signal": signal,
                "trust_net": trust_net,
                "dealer_net": dealer_net,
                "foreign_net": foreign_net,
                "weighted_score": round(net_score, 0),
                "source": "taifex_api",
            }

        # 備選方案：用法人現貨買賣超推估期貨信號
        from src.advisor.data import fetch_institutional
        inst = fetch_institutional(days=1)
        if inst is not None and not inst.empty:
            # 取最新一日的法人買賣超淨額
            trust_cols = [c for c in inst.columns if c.endswith("_trust")]
            if trust_cols:
                trust_net = int(inst[trust_cols[-1]].sum())
                signal = "bullish" if trust_net > 0 else "bearish" if trust_net < 0 else "neutral"
                return {
                    "taifex_signal": signal,
                    "trust_net": trust_net,
                    "dealer_net": 0,
                    "foreign_net": 0,
                    "weighted_score": round(trust_net, 0),
                    "source": "institutional_proxy",
                }

        # 最後備選
        return {
            "taifex_signal": "neutral",
            "trust_net": 0,
            "dealer_net": 0,
            "foreign_net": 0,
            "weighted_score": 0.0,
            "source": "fallback",
        }

    except Exception as e:
        print(f"[-] 籌碼信號失敗: {e}")
        return {"taifex_signal": "neutral", "source": "error"}


def main() -> None:
    asof = dt.date.fromisoformat(ASOF)
    regime = get_regime(asof)

    # 美股信號
    us_sig = get_us_overnight_signal()

    # 期交所籌碼
    taifex_sig = get_taifex_signal(asof)

    # 下載加權指數（作為台指期基準）
    # period=5d：假日/收盤後 1d 會拿到空資料，取近 5 天的最後一筆收盤
    try:
        twii = yf.download("^TWII", period="5d", progress=False)
        if isinstance(twii.columns, pd.MultiIndex):
            twii.columns = twii.columns.get_level_values(0)
        twii_close = float(twii["Close"].iloc[-1]) if not twii.empty else None
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
