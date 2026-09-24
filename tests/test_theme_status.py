"""API timeout 不得顯示成「無明顯題材」。"""
from src.classifier import aggregate_theme_status
from src.pipeline import theme_headline
from send_daily_line import _format_breakout


def test_aggregate_timeout_wins_over_success():
    assert aggregate_theme_status(["ok", "timeout"]) == "timeout"
    assert aggregate_theme_status(["ok"], consolidate_timeout=True) == "timeout"
    assert aggregate_theme_status(["empty", "empty"]) == "empty"
    assert aggregate_theme_status(["ok", "ok"]) == "ok"


def test_headline_timeout_is_not_no_theme():
    assert theme_headline({"themes": [], "theme_status": "timeout"}) == "timeout"
    assert theme_headline({
        "themes": [{"name": "記憶體"}],
        "theme_status": "timeout",
    }) == "記憶體（timeout）"
    assert theme_headline({"themes": [], "theme_status": "empty"}) == "無明顯題材"


def test_breakout_line_says_timeout():
    text = _format_breakout({
        "theme_status": "timeout",
        "themes": [{"name": "未分類", "fired_today_count": 11, "stocks": []}],
    })
    assert text == "🔥 族群突破：timeout"
    assert "無族群點火" not in text
