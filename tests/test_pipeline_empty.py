"""0 檔通過篩選時的管線行為（離線，monkeypatch 掉所有連網與選股）。

回歸守護 2026-07-28 的狀況：台股市場轉弱、0 檔通過門檻。舊版在 0 檔時
提前 return、跳過 _save，導致 latest_tw.json 停在前一日、前端誤判為排程
失敗。現版應仍寫入當日 latest 與日期檔（screened=[]），讓前端顯示
「今日無標的」並更新日期。
"""
import json
from datetime import datetime

import pandas as pd

from src import pipeline


def test_zero_screened_still_writes_dated_file(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "RESULT_DIR", tmp_path)

    uni = pd.DataFrame([{"code": "2330", "yahoo": "2330.TW"}])
    monkeypatch.setattr(pipeline.adv_data, "get_universe", lambda market="TW": uni)
    monkeypatch.setattr(pipeline.adv_data, "fetch_history", lambda tickers: {})
    monkeypatch.setattr(pipeline.adv_data, "patch_latest_bar", lambda h, u: None)
    monkeypatch.setattr(pipeline.adv_data, "fetch_institutional", lambda **k: None)
    monkeypatch.setattr(pipeline.adv_data, "fetch_revenue", lambda: None)
    monkeypatch.setattr(pipeline.adv_market, "get_regime",
                        lambda market="TW": {"label": "中性", "threshold": 75})
    # 核心：screener 回空 screened_df（模擬 0 檔通過門檻）+ 一個非空 universe_df
    monkeypatch.setattr(pipeline.adv_screener, "run_screen",
                        lambda *a, **k: (pd.DataFrame(), uni.rename(columns={"code": "代號"})))

    payload = pipeline.run(market="TW", classify=False, verbose=False)

    today = datetime.now(pipeline.TW_TZ).date().isoformat()
    # 回傳 payload 正確
    assert payload["date"] == today
    assert payload["screened"] == []
    assert payload["themes"] == []
    # 關鍵回歸點：latest 與日期檔都要落地，且日期為今天（不再停在前一日）
    latest = json.loads((tmp_path / "latest_tw.json").read_text(encoding="utf-8"))
    assert latest["date"] == today
    assert latest["screened"] == []
    assert (tmp_path / f"{today}_tw.json").exists()
    # available_dates 應含今天，前端「歷史回顧」才選得到這天
    dates = json.loads((tmp_path / "available_dates_tw.json").read_text(encoding="utf-8"))
    assert today in dates
