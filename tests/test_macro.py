"""總經三態與 0050 交叉：凍結 fixture，不連網。"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.advisor.macro import (
    ACTION_BUY_DIP,
    ACTION_CASH,
    ACTION_DCA,
    ACTION_HOLD_DCA,
    ACTION_TRIM,
    ACTION_WAIT,
    REGIME_DETERIORATING,
    REGIME_FAVORABLE,
    REGIME_UNFAVORABLE,
    classify_action,
    evaluate,
    evaluate_regime,
    format_alert,
    parse_observations,
    parse_pairs,
)


def _seq(end: str, values: list[float], step_days: int = 1) -> list[tuple[date, float]]:
    """以 end 為最後一日，往前推 step_days。"""
    last = date.fromisoformat(end)
    n = len(values)
    return [(last - timedelta(days=step_days * (n - 1 - i)), v) for i, v in enumerate(values)]


def _favorable() -> dict:
    """Sahm 低、曲線正、HY 安靜、Fed 在降。"""
    return {
        "sahm": parse_pairs([("2026-08-01", 0.23)]),
        "curve": _seq("2026-09-04", [0.12, 0.15, 0.14, 0.16, 0.18]),
        "hy": _seq("2026-09-04", [320.0] * 70),
        "fedfunds": parse_pairs([("2026-03-01", 4.50), ("2026-09-01", 4.25)]),
    }


def _check(label: str, cond: bool) -> bool:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}")
    return cond


def main() -> int:
    failed = 0

    print("=== FRED HY 百分點 → bp ===")
    from src.advisor.macro import SERIES_HY
    hy_pct = parse_observations([{"date": "2026-09-03", "value": "2.6"}])
    hy_bp = [(d, v * 100.0) for d, v in hy_pct]
    if not _check("2.6% → 260bp", hy_bp == [(date(2026, 9, 3), 260.0)]):
        failed += 1
    if not _check("series id 對得上", SERIES_HY == "BAMLH0A0HYM2"):
        failed += 1

    print("\n=== parse_observations 略過缺值 ===")
    parsed = parse_observations([
        {"date": "2026-09-01", "value": "."},
        {"date": "2026-09-02", "value": "0.15"},
        {"date": "2026-09-03", "value": "bad"},
    ])
    if not _check("只留 0.15", parsed == [(date(2026, 9, 2), 0.15)]):
        failed += 1

    print("\n=== Sahm 觸發 → 差 ===")
    s = _favorable()
    s["sahm"] = parse_pairs([("2026-08-01", 0.62)])
    r = evaluate(s, "green")
    if not _check("regime=unfavorable", r["regime"] == REGIME_UNFAVORABLE):
        failed += 1
    if not _check("action=trim（綠燈）", r["action"] == ACTION_TRIM):
        failed += 1
    if not _check("legs.sahm.triggered", r["legs"]["sahm"]["triggered"] is True):
        failed += 1

    print("\n=== 曲線倒掛 5 日 → 警戒 ===")
    s = _favorable()
    s["curve"] = _seq("2026-09-04", [-0.10, -0.12, -0.08, -0.15, -0.11])
    r = evaluate(s, "green")
    if not _check("regime=deteriorating", r["regime"] == REGIME_DETERIORATING):
        failed += 1
    if not _check("inverted=True", r["legs"]["curve"]["inverted"] is True):
        failed += 1

    print("\n=== 不足 5 日不判倒掛 ===")
    s = _favorable()
    s["curve"] = _seq("2026-09-04", [-0.10, -0.12])
    r = evaluate(s, "green")
    if not _check("不因 2 日負值判倒掛", r["legs"]["curve"]["inverted"] is False):
        failed += 1
    if not _check("仍為好（其餘腿乾淨）", r["regime"] == REGIME_FAVORABLE):
        failed += 1

    print("\n=== HY 危機 → 差 ===")
    s = _favorable()
    s["hy"] = _seq("2026-09-04", [650.0] * 70)
    r = evaluate(s, "red")
    if not _check("regime=unfavorable", r["regime"] == REGIME_UNFAVORABLE):
        failed += 1
    if not _check("action=cash（紅燈）", r["action"] == ACTION_CASH):
        failed += 1

    print("\n=== HY 450 或 3 個月擴大 > 80 → 警戒 ===")
    s = _favorable()
    s["hy"] = _seq("2026-09-04", [480.0] * 70)
    r = evaluate(s, "green")
    if not _check("HY 480 → deteriorating", r["regime"] == REGIME_DETERIORATING):
        failed += 1

    s = _favorable()
    # 70 日步長 1：第 0 日=250，最後=350，中間約 90 日前仍接近 250
    s["hy"] = _seq("2026-09-04", [250.0] * 40 + [350.0] * 30)
    r = evaluate(s, "green")
    widen = r["legs"]["hy"]["widen_3m"]
    if not _check(f"HY 擴大 {widen} > 80 → deteriorating",
                  r["regime"] == REGIME_DETERIORATING and widen is not None and widen > 80):
        failed += 1

    print("\n=== Fed 升息：好降警戒，不單獨判差 ===")
    s = _favorable()
    s["fedfunds"] = parse_pairs([("2026-03-01", 4.50), ("2026-09-01", 5.25)])
    r = evaluate(s, "green")
    if not _check("升息 → deteriorating", r["regime"] == REGIME_DETERIORATING):
        failed += 1
    if not _check("hiking_6m=True", r["legs"]["fedfunds"]["hiking_6m"] is True):
        failed += 1
    if not _check("不是 unfavorable", r["regime"] != REGIME_UNFAVORABLE):
        failed += 1

    print("\n=== 0050 交叉（3 態 × 綠／非綠）===")
    cases = [
        (REGIME_FAVORABLE, "green", ACTION_DCA),
        (REGIME_FAVORABLE, "yellow", ACTION_BUY_DIP),
        (REGIME_FAVORABLE, "red", ACTION_BUY_DIP),
        (REGIME_DETERIORATING, "green", ACTION_HOLD_DCA),
        (REGIME_DETERIORATING, "yellow", ACTION_WAIT),
        (REGIME_DETERIORATING, "red", ACTION_WAIT),
        (REGIME_UNFAVORABLE, "green", ACTION_TRIM),
        (REGIME_UNFAVORABLE, "red", ACTION_CASH),
    ]
    for regime, signal, expect in cases:
        got = classify_action(regime, signal)
        if not _check(f"{regime} × {signal} → {expect}", got == expect):
            failed += 1

    print("\n=== 文案：紅燈 + 越跌越買 覆蓋停損 ===")
    r = evaluate(_favorable(), "red")
    text = format_alert(r, {"regime": REGIME_FAVORABLE, "action": ACTION_DCA})
    if not _check("含越跌越買", "越跌越買" in text):
        failed += 1
    if not _check("含覆蓋停損", "此則覆蓋 0050 停損建議" in text):
        failed += 1
    voo_text = format_alert(r, {"regime": REGIME_FAVORABLE, "action": ACTION_DCA},
                            code="VOO")
    if not _check("VOO 標題", "【VOO｜越跌越買】" in voo_text):
        failed += 1
    if not _check("VOO 覆蓋停損", "此則覆蓋 VOO 停損建議" in voo_text):
        failed += 1

    print("\n=== 文案：總經態改變 ===")
    s = _favorable()
    s["sahm"] = parse_pairs([("2026-08-01", 0.62)])
    r = evaluate(s, "green")
    text = format_alert(r, {"regime": REGIME_FAVORABLE, "action": ACTION_DCA})
    if not _check("含由好轉差", "總經由好轉差" in text):
        failed += 1
    if not _check("綠燈差不含覆蓋句", "此則覆蓋" not in text):
        failed += 1

    print("\n=== 缺序列不崩、不誤觸 ===")
    r = evaluate({"sahm": [], "curve": [], "hy": [], "fedfunds": []}, "green")
    if not _check("全缺仍回 favorable（無觸發）", r["regime"] == REGIME_FAVORABLE):
        failed += 1
    if not _check("sahm status=missing", r["legs"]["sahm"]["status"] == "missing"):
        failed += 1

    print("\n=== etf_alert 狀態機（mock load_regime，不連網）===")
    import src.advisor.macro as macro_mod
    from etf_alert import _check_macro_action

    frozen = evaluate_regime(_favorable())
    frozen["date"] = "2026-09-06"
    macro_mod.load_regime = lambda **kwargs: frozen

    both_green = {"etfs": {"0050.TW": "green", "VOO": "green"}}
    alerts: list[str] = []
    new_state: dict = dict(both_green)
    _check_macro_action({}, new_state, alerts, dry_run=True)
    if not _check("首次只建基準、無警報",
                  alerts == [] and new_state["macro"]["action"] == ACTION_DCA
                  and new_state["macro"]["etfs"]["VOO"]["action"] == ACTION_DCA):
        failed += 1

    alerts = []
    new_state = dict(both_green)
    _check_macro_action(
        {"macro": {"regime": REGIME_FAVORABLE, "action": ACTION_DCA,
                   "etfs": {"0050.TW": {"action": ACTION_DCA, "signal": "green"},
                            "VOO": {"action": ACTION_DCA, "signal": "green"}}}},
        new_state, alerts, dry_run=True,
    )
    if not _check("相同態不推", alerts == []):
        failed += 1

    alerts = []
    new_state = {"etfs": {"0050.TW": "yellow", "VOO": "green"}}
    _check_macro_action(
        {"macro": {"regime": REGIME_FAVORABLE, "action": ACTION_DCA,
                   "etfs": {"0050.TW": {"action": ACTION_DCA, "signal": "green"},
                            "VOO": {"action": ACTION_DCA, "signal": "green"}}}},
        new_state, alerts, dry_run=True,
    )
    if not _check("只有 0050 action 變 → 一則 0050",
                  len(alerts) == 1 and "【0050｜越跌越買】" in alerts[0]):
        failed += 1

    alerts = []
    new_state = {"etfs": {"0050.TW": "green", "VOO": "yellow"}}
    _check_macro_action(
        {"macro": {"regime": REGIME_FAVORABLE, "action": ACTION_DCA,
                   "etfs": {"0050.TW": {"action": ACTION_DCA, "signal": "green"},
                            "VOO": {"action": ACTION_DCA, "signal": "green"}}}},
        new_state, alerts, dry_run=True,
    )
    if not _check("只有 VOO action 變 → 一則 VOO",
                  len(alerts) == 1 and "【VOO｜越跌越買】" in alerts[0]):
        failed += 1

    alerts = []
    new_state = {"etfs": {"0050.TW": "green", "VOO": "yellow"}}
    _check_macro_action(
        {"macro": {"regime": REGIME_FAVORABLE, "action": ACTION_DCA}},
        new_state, alerts, dry_run=True,
    )
    if not _check("舊版 state：0050 不變不推、VOO 首次只建基準",
                  alerts == []):
        failed += 1

    print(f"\n=== 結果：failed={failed} ===")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
