"""覆盤尺：Top3 vs 股票池命中率（離線、不連網）。"""
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from weekly_review import (  # noqa: E402
    _hit_rate_from_closes,
    lift_pp,
    lost_to_universe,
    parse_select_window,
    primary_win_rate,
)


def _ms(top3_wr, univ_wr, top3_n=5, all_wr=None):
    return {
        "win_rate": all_wr if all_wr is not None else top3_wr,
        "segments": {"top3": {"win_rate": top3_wr, "total": top3_n}},
        "benchmark": {"universe_hit_rate": univ_wr},
    }


def test_primary_uses_top3():
    assert primary_win_rate(_ms(45.5, 40.3, all_wr=38.8)) == 45.5


def test_down_market_beat_pool_is_pass():
    """空頭週池子 20% 上漲、Top3 45% → 找到相對強勢，不可當失敗。"""
    ms = _ms(45.0, 20.0)
    assert lift_pp(ms) == 25.0
    assert lost_to_universe(ms) is False


def test_down_market_no_skill_is_fail():
    """空頭週池子 26%、Top3 18% → 沒抓到那群往上的。"""
    ms = _ms(18.0, 26.0)
    assert lift_pp(ms) == -8.0
    assert lost_to_universe(ms) is True


def test_equal_is_fail():
    """P=U 沒有超額，算輸給池子。"""
    assert lost_to_universe(_ms(40.0, 40.0)) is True


def test_missing_universe_is_unknown():
    ms = {"segments": {"top3": {"win_rate": 40.0, "total": 3}}, "benchmark": {}}
    assert lost_to_universe(ms) is None
    assert lift_pp(ms) is None


def test_parse_select_window():
    assert parse_select_window("本週 (08-17 ~ 08-21)", "2026-08-22") == (
        date(2026, 8, 17),
        date(2026, 8, 21),
    )


def test_hit_rate_from_closes():
    idx = pd.to_datetime(["2026-08-17", "2026-08-21"])
    closes = {
        "up": pd.Series([100.0, 110.0], index=idx),
        "down": pd.Series([100.0, 90.0], index=idx),
        "flat": pd.Series([100.0, 100.0], index=idx),
    }
    s = _hit_rate_from_closes(closes, date(2026, 8, 17), date(2026, 8, 21))
    assert s["universe_n"] == 3
    assert s["universe_up"] == 1
    assert s["universe_hit_rate"] == 33.3


if __name__ == "__main__":
    test_primary_uses_top3()
    test_down_market_beat_pool_is_pass()
    test_down_market_no_skill_is_fail()
    test_equal_is_fail()
    test_missing_universe_is_unknown()
    test_parse_select_window()
    test_hit_rate_from_closes()
    print("ok")
