# -*- coding: utf-8 -*-
"""季底作帳選股（法人季度結算行情）：投信季底前大買股

回測結論（12 個季度、單檔日線對照加權指數，手續費成本 0.585%）：
- 投信季底前 5～3 個交易日「淨買超 ≥ 7 天」的股票，隔日開盤進場，
  T+5 收盤前出場，期望 +1.45%、超額 α -1.92%（與大盤同漲，無超額但穩定）
- 外資訊號相似，但聯動性更強（跟著大盤動），推薦重點監控「投信」
- 進場時機早於季底 5～3 天為黃金期；季底前 12 天進場滑點明顯；
  季底當日進場反而變負（搶快無益）
- 若已持有投信大買股，季底「不要急著出」——T+10 持有期會多賺平均 +6.76%
  （但 1/3 股會續跌，需搭配停損）
- 停損：進場價 -10%（試算最穩健）；進場後期望最低 -1.5%
- 季節性：僅限 3、6、9、12 月季底（各月最後 5 個交易日前後）；其他月份無效訊號

用法：python screen_quarter.py [YYYY-MM-DD]
輸出：data/results/quarter_tw.json + CSV
相依：pandas yfinance
"""
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

from src.advisor.data import fetch_institutional

REPO = Path(__file__).resolve().parent
ASOF = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") \
    else dt.date.today().isoformat()
OUT_JSON = REPO / "data" / "results" / "quarter_tw.json"

STOP_PCT = 0.10      # 停損：進場參考價 -10%（試算最穩健；回測 P25 MAE ~-12%）
SIG_DAYS = 10        # 訊號回看交易日數
MIN_BUY_DAYS = 7     # 買超天數門檻
TOP_N = 10           # 每季選前 N 檔
ENTRY_OFFSET = 5     # 進場：季底前 N 個交易日的隔日開盤
EXIT_OFFSET = 5      # 出場：進場後 N 日收盤（T+5 勝率最高）


def season_phase(d: dt.date) -> str:
    """判斷是否為季底窗口（季度最後月份的最後 20 日）"""
    q_month = (1, 4, 7, 10)[d.month // 3] if d.month % 3 == 0 else None
    if q_month is None:
        return "off"
    _, last_day = (d.replace(day=1) + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
    if d.day >= last_day - 20:
        return "active"
    if d.day >= last_day - 25:
        return "preview"
    return "off"


def main() -> None:
    asof = dt.date.fromisoformat(ASOF)
    phase = season_phase(asof)

    payload = {
        "date": ASOF,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "phase": phase,
        "params": {
            "stop_pct": STOP_PCT,
            "min_buy_days": MIN_BUY_DAYS,
            "top_n": TOP_N,
            "entry": f"季底前 {ENTRY_OFFSET} 日隔日開盤",
            "exit": f"進場後 {EXIT_OFFSET} 日收盤",
        },
        "stocks": [],
    }

    if phase == "off":
        payload["note"] = "非季底窗口（限 3/6/9/12 月最後 20 日）"
        OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print(f"非季節（{ASOF}），已寫入 off 狀態")
        return

    # 獲取最近 20 個交易日的法人資料（回看 SIG_DAYS，加上往後 ENTRY_OFFSET）
    inst = fetch_institutional(days=SIG_DAYS + ENTRY_OFFSET, lookback=ENTRY_OFFSET + 40)
    if inst is None:
        payload["note"] = "法人資料不足，無法判斷"
        OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print("法人資料不足，結束")
        return

    # 列出已決策日期（ASOF 往回 ENTRY_OFFSET+1 天內的第一個交易日）
    inst_cols = [c for c in inst.columns if c.endswith("_trust")]
    trading_days = sorted([c[-8:] for c in inst_cols])
    decision_day = None
    for td in reversed(trading_days):
        d = dt.datetime.strptime(td, "%Y%m%d").date()
        if d <= asof - dt.timedelta(days=ENTRY_OFFSET):
            decision_day = td
            break
    if decision_day is None:
        payload["note"] = "尚未進入決策窗口"
        OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print("無決策日期，結束")
        return

    # 下載股價（2 年歷史 + 期貨預測）
    with open(REPO / "data" / "results" / "universe_tw.json", encoding="utf-8") as f:
        uni = {s["stock_id"]: s for s in json.load(f)["stocks"]}
    codes = list(uni.keys())
    tickers = {c: c + ".TW" for c in codes}
    print(f"下載 {len(codes)} 檔 2 年日線 ...", flush=True)
    raw = yf.download(list(tickers.values()), period="2y", group_by="ticker",
                      auto_adjust=True, progress=False, threads=True)

    prices = {}
    for c, tk in tickers.items():
        try:
            df = raw[tk].dropna(subset=["Close"]).loc[:asof]
        except KeyError:
            continue
        if len(df) >= 20:
            prices[c] = df

    # 計算訊號：決策日往回 SIG_DAYS 內的投信買超
    stocks_out = []
    for code in codes:
        if code not in inst.index or code not in prices:
            continue
        # 取決策日前 SIG_DAYS 的投信訊號
        trust_cols = [c for c in inst.columns if c.endswith("_trust")
                      and c[:-6] <= decision_day]
        if len(trust_cols) < SIG_DAYS:
            continue
        trust_cols = trust_cols[-SIG_DAYS:]
        buys = sum(1 for c in trust_cols if inst.loc[code, c] > 0)
        if buys < MIN_BUY_DAYS:
            continue
        net = sum(inst.loc[code, c] for c in trust_cols)
        vol = sum(prices[code]["Volume"].loc[
            pd.Timestamp(c[:-6]):pd.Timestamp(c[:-6])] for c in trust_cols)
        if vol <= 0:
            continue
        ratio = net / vol

        # 進場、出場日期
        entry_day_i = trading_days.index(decision_day) + ENTRY_OFFSET + 1
        exit_day_i = entry_day_i + EXIT_OFFSET
        if entry_day_i >= len(prices[code]) or exit_day_i >= len(prices[code]):
            continue
        entry_day = prices[code].index[entry_day_i]
        exit_day = prices[code].index[exit_day_i]

        entry = float(prices[code].loc[entry_day, "Open"])
        exit_close = float(prices[code].loc[exit_day, "Close"])
        current = float(prices[code].iloc[-1])
        pnl_pct = (current / entry - 1) * 100 if entry > 0 else None

        rec = {
            "stock_id": code,
            "stock_name": uni[code].get("stock_name", ""),
            "buy_days": int(buys),
            "buy_ratio": round(ratio, 4),
            "entry_ref": round(entry, 2),
            "exit_plan": round(exit_close, 2),
            "stop_line": round(entry * (1 - STOP_PCT), 2),
            "current": round(current, 2),
            "pnl_pct": round(pnl_pct, 1) if pnl_pct is not None else None,
            "stop_hit": bool(current < entry * (1 - STOP_PCT)),
        }
        stocks_out.append(rec)

    stocks_out.sort(key=lambda r: -r["buy_ratio"])
    stocks_out = stocks_out[:TOP_N]
    payload["stocks"] = stocks_out
    payload["decision_date"] = decision_day
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"phase={phase} 名單 {len(stocks_out)} 檔 → {OUT_JSON}")

    if stocks_out:
        csv = REPO / "data" / "results" / f"screen_quarter_result_{ASOF.replace('-', '')}.csv"
        pd.DataFrame(stocks_out).to_csv(csv, index=False, encoding="utf-8-sig")
        print(f"已存檔：{csv}")


if __name__ == "__main__":
    main()
