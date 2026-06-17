"""均線篩選：收盤是否站上所有指定均線（20/60/120/240）。

輸入為各股的歷史收盤序列（由 pipeline 用 data_source.get_stock_history 取得）。
"""
from __future__ import annotations

import pandas as pd


def ma_status_for_closes(closes: pd.Series, windows: list[int]) -> dict:
    """回傳單股的各均線值與 above_all 判斷。

    資料不足最長均線天數者，該均線為 None 且 above_all=False（無法確認長期趨勢）。
    """
    closes = closes.reset_index(drop=True)
    last_close = float(closes.iloc[-1]) if not closes.empty else None
    row: dict = {"close": last_close}
    above_all = last_close is not None
    for w in windows:
        if last_close is not None and len(closes) >= w:
            ma = round(float(closes.tail(w).mean()), 2)
            row[f"ma_{w}"] = ma
            if last_close < ma:
                above_all = False
        else:
            row[f"ma_{w}"] = None
            above_all = False
    row["above_all"] = above_all
    return row


def screen(histories: dict[str, pd.DataFrame], windows: list[int]) -> pd.DataFrame:
    """histories: {stock_id: 日K DataFrame(含 close 欄)} -> 各股均線狀態表。"""
    rows = []
    for sid, hist in histories.items():
        if hist is None or hist.empty or "close" not in hist:
            continue
        status = ma_status_for_closes(hist["close"], windows)
        status["stock_id"] = sid
        rows.append(status)
    return pd.DataFrame(rows)
