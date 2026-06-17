"""漲幅排行：取當日漲幅前 N 名。"""
from __future__ import annotations

import pandas as pd


def top_gainers(market_today: pd.DataFrame, top_n: int) -> pd.DataFrame:
    """market_today 已含 change_pct（由 data_source.get_market_today 計算）。"""
    return (
        market_today.sort_values("change_pct", ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )
