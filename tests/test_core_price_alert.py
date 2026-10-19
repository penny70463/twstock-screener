"""0050／VOO 價燈不得再下與總經相反的買賣指令。"""
from etf_alert import core_price_alert


def test_core_dip_states_the_cross_without_a_buy():
    text = core_price_alert("green", "yellow", "0050.TW", "元大台灣50", 108.15, 108.83, 90)
    assert "跌破季線" in text
    assert "50 日均線之下" in text
    assert "逢低" not in text
    assert "買" not in text
    assert "加碼" not in text


def test_core_reclaim_and_break_are_facts():
    up = core_price_alert("yellow", "green", "VOO", "S&P 500", 701.61, 705.21, 600)
    assert "站回季線" in up
    assert "加碼" not in up
    down = core_price_alert("yellow", "red", "0050.TW", "元大台灣50", 80, 90, 85)
    assert "跌破年線" in down
    assert "停損" not in down
