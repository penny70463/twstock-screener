"""單檔部位上限測試（離線）：風險法張數不得超過 CAPITAL × MAX_POSITION_WEIGHT × 水位

用法: python tests/test_position_size.py
"""
import sys
sys.path.insert(0, ".")

from src.advisor import config
from src.advisor.scoring import position_size


def _weight(units: int, close: float, unit: int) -> float:
    return units * unit * close / config.CAPITAL


def test_low_atr_capped():
    # 9933 實例（2026-10-02）：收盤 45.8，舊公式短線 12 張 ≈ 資金 55%
    close, stop = 45.8, 45.8 - 1.2
    lots = position_size(close, stop, exposure=1.0, unit=1000)
    assert _weight(lots, close, 1000) <= config.MAX_POSITION_WEIGHT + 1e-9, lots
    assert lots == 3, lots  # 1,000,000 × 15% / 45.8 / 1000 = 3.27 → 3 張


def test_exposure_scales_cap():
    close, stop = 45.8, 45.8 - 1.2
    assert position_size(close, stop, exposure=0.5, unit=1000) == 1  # 7.5% → 1.63 張
    assert position_size(close, stop, exposure=0.0, unit=1000) == 0


def test_risk_binds_when_stop_wide():
    # 停損距離大時由風險法決定：15,000 / 20 = 750 股 < 權重上限 1,500 股
    assert position_size(100.0, 80.0, exposure=1.0, unit=1) == 750


def test_exposure_clipped():
    assert position_size(100.0, 99.0, exposure=1.7, unit=1) == position_size(100.0, 99.0, 1.0, unit=1)
    assert position_size(100.0, 99.0, exposure=-0.3, unit=1) == 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  [PASS] {name}")
    print("all passed")
