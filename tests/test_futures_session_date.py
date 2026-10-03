"""週末補跑不得把台指期 date 蓋成行事曆今天。"""
from screen_futures import session_date


def test_weekend_stamp_uses_taifex_session():
    assert session_date("2026-10-03", "2026-10-02", "2026-10-02") == "2026-10-02"


def test_falls_back_to_twii_bar_then_asof():
    assert session_date("2026-10-03", None, "2026-10-02") == "2026-10-02"
    assert session_date("2026-10-02", None, None) == "2026-10-02"
