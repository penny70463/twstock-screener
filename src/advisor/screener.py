"""篩選引擎：跨個股計算 RS 百分位 → 硬性排除 → 五維度評分 → 排序"""

import pandas as pd

from . import scoring


def run_screen(universe: pd.DataFrame,
               history: dict[str, pd.DataFrame],
               inst: pd.DataFrame | None,
               revenue: pd.DataFrame | None,
               threshold: float,
               market: str = "TW") -> tuple[pd.DataFrame, pd.DataFrame]:
    """回傳 (過濾後的股票, 全股票池評分)"""
    meta = universe.set_index("code")

    # RS 百分位必須跨「整個股票池」計算，這是相對強度的意義所在
    rs_values = pd.Series({code: scoring.rs_raw(df) for code, df in history.items()})
    rs_pct = rs_values.rank(pct=True) * 100

    rows = []
    universe_rows = []
    for code, df in history.items():
        try:
            # 修改以取得 hard_filter 失敗原因
            ok, reason = scoring.hard_filter(df)
            if not ok:
                result = {
                    "總分": 0.0, "趨勢": 0.0, "動能": 0.0, "量能": 0.0, "籌碼": 0.0, "營收": 0.0,
                    "RS": 0.0, "短線停損": 0.0, "短線張數": 0, "波段停損": 0.0, "波段張數": 0,
                    "長線停損": 0.0, "長線張數": 0, "訊號": f"未通過: {reason}", "sparkline": []
                }
            else:
                result = scoring.score_stock(code, df, float(rs_pct[code]), inst, revenue)
        except Exception:
            continue
            
        if result is None:
            continue
            
        industry = ""
        if revenue is not None and code in revenue.index:
            industry = str(revenue.loc[code].get("industry", "") or "")
        elif code in meta.index and "industry" in meta.columns:
            industry = str(meta.at[code, "industry"] or "")
            
        row_data = {
            "代號": code,
            "名稱": meta.at[code, "name"] if code in meta.index else "",
            "市場": meta.at[code, "market"] if code in meta.index else "",
            "產業": industry,
            "收盤價": float(df["Close"].iloc[-1]) if len(df) > 0 else 0.0,
            "RS": round(float(rs_pct[code]), 0),
            "sparkline": [round(float(x), 2) for x in df["Close"].tail(20).tolist()] if len(df) > 0 else [],
            **result,
        }
        universe_rows.append(row_data)
        
        if result["總分"] >= threshold:
            rows.append(row_data)

    if not universe_rows:
        return pd.DataFrame(), pd.DataFrame()

    out = pd.DataFrame(rows).sort_values("總分", ascending=False) if rows else pd.DataFrame()
    universe_out = pd.DataFrame(universe_rows)
    cols = ["代號", "名稱", "市場", "產業", "收盤價", "總分", "趨勢", "動能",
            "量能", "籌碼", "營收", "RS", "短線停損", "短線張數", "波段停損", "波段張數", "長線停損", "長線張數", "訊號", "sparkline"]
            
    if not out.empty:
        out = out[cols].reset_index(drop=True)
    if not universe_out.empty:
        universe_out = universe_out[cols].reset_index(drop=True)
        
    return out, universe_out
