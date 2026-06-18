"""篩選引擎：跨個股計算 RS 百分位 → 硬性排除 → 五維度評分 → 排序"""

import pandas as pd

from . import scoring


def run_screen(universe: pd.DataFrame,
               history: dict[str, pd.DataFrame],
               inst: pd.DataFrame | None,
               revenue: pd.DataFrame | None,
               threshold: float) -> pd.DataFrame:
    """回傳總分 >= threshold 的股票，依總分排序"""
    meta = universe.set_index("code")

    # RS 百分位必須跨「整個股票池」計算，這是相對強度的意義所在
    rs_values = pd.Series({code: scoring.rs_raw(df) for code, df in history.items()})
    rs_pct = rs_values.rank(pct=True) * 100

    rows = []
    for code, df in history.items():
        try:
            result = scoring.score_stock(code, df, float(rs_pct[code]), inst, revenue)
        except Exception:
            continue
        if result is None or result["總分"] < threshold:
            continue
        industry = ""
        if revenue is not None and code in revenue.index:
            industry = str(revenue.loc[code].get("industry", "") or "")
        rows.append({
            "代號": code,
            "名稱": meta.at[code, "name"] if code in meta.index else "",
            "市場": meta.at[code, "market"] if code in meta.index else "",
            "產業": industry,
            "RS": round(float(rs_pct[code]), 0),
            **result,
        })

    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows).sort_values("總分", ascending=False)
    cols = ["代號", "名稱", "市場", "產業", "收盤價", "總分", "趨勢", "動能",
            "量能", "籌碼", "營收", "RS", "停損價", "建議張數", "訊號"]
    return out[cols].reset_index(drop=True)
