"""美股動能 screener 的離線單元測試（合成資料，不連網）

鎖定核心契約，避免未來改動破壞策略或前端相容性：
  1. 輸出欄位與 advisor.screener.run_screen 完全一致（前端契約）
  2. 絕對動能濾網：跌破 200 日線者不入選（screened），但仍在 universe
  3. ETF 不參與 screened 排名，但仍計入 universe（供存股體檢）
  4. 動能越強 → RS / 動能分越高，且 screened 依總分遞減排序
"""

import numpy as np
import pandas as pd
import pytest

from src.advisor import us_screener
from src.advisor.us_screener import OUT_COLS


def _series(start: float, daily_drift: float, n: int = 320, seed: int = 0) -> pd.DataFrame:
    """合成日線：固定漂移 + 小幅雜訊，產生可控的趨勢/動能。"""
    rng = np.random.default_rng(seed)
    rets = daily_drift + rng.normal(0, 0.005, n)
    close = start * np.cumprod(1 + rets)
    idx = pd.bdate_range(end=pd.Timestamp("2026-06-18"), periods=n)
    return pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.99,
        "Close": close, "Volume": rng.integers(1_000_000, 2_000_000, n),
    }, index=idx)


@pytest.fixture
def scenario():
    history = {
        "STRONG": _series(50, 0.0020, seed=1),   # 強勢上升、動能最高
        "MILD": _series(50, 0.0008, seed=2),     # 溫和上升
        "WEAK": _series(120, -0.0015, seed=3),   # 下降、跌破 200 日線
        "QQQ": _series(300, 0.0015, seed=4),     # 上升的 ETF
    }
    universe = pd.DataFrame([
        {"code": "STRONG", "name": "Strong Co", "industry": "Information Technology"},
        {"code": "MILD", "name": "Mild Co", "industry": "Industrials"},
        {"code": "WEAK", "name": "Weak Co", "industry": "Utilities"},
        {"code": "QQQ", "name": "QQQ ETF", "industry": "ETF"},
    ])
    screened, full = us_screener.run_screen(universe, history, threshold=60.0)
    return screened, full


def test_schema_matches_frontend_contract(scenario):
    screened, full = scenario
    assert list(full.columns) == OUT_COLS
    assert list(screened.columns) == OUT_COLS
    # 美股無籌碼/營收 → 永遠 None（前端欄位留空的契約）
    assert full["籌碼"].isna().all() and full["營收"].isna().all()


def test_absolute_momentum_filter(scenario):
    screened, full = scenario
    codes = set(screened["代號"])
    assert "STRONG" in codes                      # 趨勢強者入選
    assert "WEAK" not in codes                     # 跌破 200 日線者出局
    assert "WEAK" in set(full["代號"])             # 但仍在 universe
    weak_row = full[full["代號"] == "WEAK"].iloc[0]
    assert "未通過" in weak_row["訊號"]


def test_etf_excluded_from_screened_but_in_universe(scenario):
    screened, full = scenario
    assert "QQQ" not in set(screened["代號"])
    assert "QQQ" in set(full["代號"])


def test_momentum_ranking_order(scenario):
    screened, _ = scenario
    # 依總分遞減排序
    scores = screened["總分"].tolist()
    assert scores == sorted(scores, reverse=True)
    # 強勢股動能分高於溫和股
    full_by_code = {r["代號"]: r for _, r in scenario[1].iterrows()}
    assert full_by_code["STRONG"]["動能"] > full_by_code["MILD"]["動能"]


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
