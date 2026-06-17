"""Streamlit 顯示頁：讀取排程產出的結果，呈現強勢股清單與題材族群。

部署在 Streamlit Community Cloud：
- 程式碼可公開，金鑰放 Streamlit Secrets（後台設定 NVIDIA_API_KEY 等）。
- 排程由 GitHub Actions 負責，本頁預設只「讀結果」，不現抓現算。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from config import RESULT_DIR

st.set_page_config(page_title="台股強勢股 + 題材族群", layout="wide")
st.title("台股每日強勢股篩選")
st.caption("漲幅前 N 名 且 站上 20/60/120/240 日均線，並依投資題材族群分類")


def list_results() -> list[str]:
    files = sorted(
        (p.stem for p in RESULT_DIR.glob("*.json") if p.stem != "latest"),
        reverse=True,
    )
    return files


def load_result(stem: str) -> dict | None:
    p = RESULT_DIR / f"{stem}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


with st.sidebar:
    st.header("資料")
    dates = list_results()
    latest = RESULT_DIR / "latest.json"
    if not dates and not latest.exists():
        st.warning("尚無結果。請先執行 `python run_pipeline.py` 產生資料。")
        st.stop()
    options = ["最新"] + dates
    choice = st.selectbox("選擇日期", options)

stem = "latest" if choice == "最新" else choice
data = load_result(stem)
if not data:
    st.error("找不到結果檔。")
    st.stop()

screened = pd.DataFrame(data.get("screened", []))
themes = data.get("themes", [])

c1, c2, c3 = st.columns(3)
c1.metric("交易日", data.get("date", "-"))
c2.metric("通過篩選檔數", len(screened))
c3.metric("題材族群數", len(themes))

generated_at = (data.get("generated_at") or "-").replace("T", " ")
st.caption(f"產生時間：{generated_at}　參數：{data.get('params', {})}")

st.info(
    "資料更新時間：每個交易日**台灣時間 18:00** 自動更新。"
    "台股 13:30 收盤，但 TWSE 盤後行情約 14:30 才齊全、"
    "FinMind 日 K（用於計算均線）約 17:30 更新，故排在 18:00 確保兩個資料源當日資料都到位。",
    icon="🕒",
)

tab1, tab2 = st.tabs(["題材族群", "完整清單"])

with tab1:
    if not themes:
        st.info("此日無題材分類結果（可能未開啟 LLM 分類）。")
    for t in themes:
        stocks = t.get("stocks", [])
        with st.expander(f"{t.get('name', '未命名')}　({len(stocks)} 檔)", expanded=True):
            st.write(t.get("reason", ""))
            if stocks:
                df = pd.DataFrame(stocks)
                if not screened.empty and "stock_id" in screened:
                    df = df.merge(
                        screened[["stock_id", "change_pct", "close"]],
                        left_on="code",
                        right_on="stock_id",
                        how="left",
                    ).drop(columns=["stock_id"], errors="ignore")
                st.dataframe(df, use_container_width=True, hide_index=True)

with tab2:
    if screened.empty:
        st.info("無通過篩選的個股。")
    else:
        cols = [
            c
            for c in [
                "stock_id",
                "stock_name",
                "industry_category",
                "change_pct",
                "close",
            ]
            + [c for c in screened.columns if c.startswith("ma_")]
            if c in screened.columns
        ]
        st.dataframe(
            screened[cols].sort_values("change_pct", ascending=False),
            use_container_width=True,
            hide_index=True,
        )
