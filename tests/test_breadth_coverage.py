"""寬度計算：單日大量缺 K 不得把市場寬度打穿。

2026-09-01 美股 Yahoo 在 8/28 缺 465/520 檔，rolling(60).mean() 的 MA 變 NaN，
(close > NaN) 全 False，寬度從 56.9% 假摔成 5.2%，曝險 85%→50% 誤報。
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.advisor.market import breadth_coverage, breadth_series
from src.advisor.data import session_gaps


def _rising_panel(n_days=80, n_names=20, seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-02", periods=n_days)
    cols = [f"S{i}" for i in range(n_names)]
    # 緩慢上漲，絕大多數日子站上 60MA
    data = 100 + np.arange(n_days)[:, None] * 0.2 + rng.normal(0, 0.05, size=(n_days, n_names))
    return pd.DataFrame(data, index=idx, columns=cols)


def _legacy_breadth(panel: pd.DataFrame) -> pd.Series:
    ma60 = panel.rolling(60).mean()
    above = (panel > ma60).sum(axis=1)
    valid = panel.notna().sum(axis=1).clip(lower=1)
    return above / valid


def test_complete_panel_matches_legacy():
    panel = _rising_panel()
    new = breadth_series(panel)
    old = _legacy_breadth(panel)
    # 滿 60 日之後，完整資料的 ffill / min_periods 不應改數字
    # （前 59 日舊公式 MA=NaN 寬度=0，新公式 min_periods=50 會先有值）
    tail = slice(60, None)
    delta = (new.iloc[tail] - old.iloc[tail]).abs()
    assert (delta < 1e-12).all(), f"完整資料寬度漂移 max={float(delta.max())}"


def test_one_day_hole_does_not_collapse_next_session():
    panel = _rising_panel()
    hole = panel.index[-2]
    last = panel.index[-1]
    broken = panel.copy()
    # 模擬 Yahoo 單日大量缺 K：只留 2 檔
    keep = ["S0", "S1"]
    for c in broken.columns:
        if c not in keep:
            broken.loc[hole, c] = np.nan

    old_last = float(_legacy_breadth(broken).loc[last])
    new_last = float(breadth_series(broken).loc[last])
    intact_last = float(breadth_series(panel).loc[last])

    assert old_last < 0.20, f"舊公式應被打穿，實際 {old_last:.1%}"
    assert abs(new_last - intact_last) < 0.05, (
        f"新公式最後一日 {new_last:.1%} 應接近無洞 {intact_last:.1%}"
    )
    assert breadth_coverage(broken) >= 0.80


def test_session_gaps_flags_majority_hole():
    idx = pd.bdate_range("2024-01-02", periods=15)
    cols = [f"S{i}" for i in range(10)]
    data = 100.0 + np.arange(15)[:, None] + np.zeros((15, 10))
    panel = pd.DataFrame(data, index=idx, columns=cols)
    history = {c: pd.DataFrame({"Close": panel[c]}) for c in cols}
    hole = idx[-2]
    for c in cols[2:]:  # 8/10 缺
        history[c].loc[hole, "Close"] = np.nan
    gaps = session_gaps(history, lookback=5, warn_pct=0.20)
    dates = {g["date"] for g in gaps}
    assert hole.date().isoformat() in dates
    assert all(g["missing"] >= 8 for g in gaps if g["date"] == hole.date().isoformat())


def test_session_gaps_silent_when_complete():
    idx = pd.bdate_range("2024-01-02", periods=12)
    history = {
        "A": pd.DataFrame({"Close": pd.Series(range(12), index=idx, dtype=float)}),
        "B": pd.DataFrame({"Close": pd.Series(range(12), index=idx, dtype=float)}),
    }
    assert session_gaps(history, lookback=5, warn_pct=0.20) == []


def test_fill_gaps_from_prior_close_writes_flat_bar():
    from src.advisor.data import fill_gaps_from_prior_close
    idx = pd.bdate_range("2024-01-02", periods=8)
    hole = idx[-2]
    hist = {}
    for name, start in (("A", 10.0), ("B", 20.0)):
        close = pd.Series(start + np.arange(8, dtype=float), index=idx)
        close.loc[hole] = float("nan")
        hist[name] = pd.DataFrame({
            "Open": close, "High": close, "Low": close, "Close": close, "Volume": 1.0,
        })
    n = fill_gaps_from_prior_close(hist, [hole.date().isoformat()])
    assert n == 2
    prev = hole - pd.tseries.offsets.BDay(1)
    for name in ("A", "B"):
        assert hist[name].loc[hole, "Close"] == hist[name].loc[prev, "Close"]
        assert hist[name].loc[hole, "Volume"] == 0
    assert session_gaps(hist, lookback=5, warn_pct=0.20) == []


def main() -> int:
    tests = [
        test_complete_panel_matches_legacy,
        test_one_day_hole_does_not_collapse_next_session,
        test_session_gaps_flags_majority_hole,
        test_session_gaps_silent_when_complete,
        test_fill_gaps_from_prior_close_writes_flat_bar,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
