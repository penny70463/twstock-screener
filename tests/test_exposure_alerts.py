"""回測 etf_alert.py 的曝險水位警報邏輯

針對台股與美股分開測試以下三種情境：
  ⚠️ 風險警報（水位大幅下降）
  📈 風險緩解（水位回升但還沒 100%）
  ✅ 警報解除（水位重回 100%）

以及各種邊界條件（微幅波動不觸發、持平不觸發、首次執行等）。
"""
import sys
sys.path.insert(0, ".")

from etf_alert import _check_exposure, EXPOSURE_CHANGE_THRESHOLD

# ---------------------------------------------------------------------------
# 測試輔助函式
# ---------------------------------------------------------------------------
def run_case(label: str, market_label: str, market_key: str,
             old_exp, curr_exp, expected_type: str):
    """
    執行單一測試案例。
    expected_type: "⚠️" | "📈" | "✅" | "NONE"
    """
    old_state = {"exposure": {market_key: old_exp}} if old_exp is not None else {}
    new_state = {"exposure": {}}
    alerts = []
    market_state = {"exposure": curr_exp, "realized_vol": 25.0}

    _check_exposure(market_label, market_key, market_state, old_state, new_state, alerts)

    # 判斷結果
    if expected_type == "NONE":
        passed = len(alerts) == 0
        actual = "NONE" if not alerts else alerts[0][:2]
    else:
        passed = len(alerts) == 1 and alerts[0].startswith(expected_type)
        actual = alerts[0][:2] if alerts else "NONE"

    status = "✅ PASS" if passed else "❌ FAIL"
    direction = ""
    if old_exp is not None:
        change = curr_exp - old_exp
        direction = f" (Δ={change:+.0%})"

    print(f"  {status}  {label}")
    print(f"         舊={fmt(old_exp)} → 新={fmt(curr_exp)}{direction}")
    if not passed:
        print(f"         期望: {expected_type}  實際: {actual}")
        if alerts:
            print(f"         訊息: {alerts[0]}")
    return passed


def fmt(v):
    return "N/A" if v is None else f"{int(v*100)}%"


# ---------------------------------------------------------------------------
# 主測試
# ---------------------------------------------------------------------------
def main():
    total = 0
    passed = 0
    threshold = EXPOSURE_CHANGE_THRESHOLD

    for market_label, market_key in [("🇹🇼 台股", "TW"), ("🇺🇸 美股", "US")]:
        print(f"\n{'='*60}")
        print(f"  {market_label} 曝險水位警報回測")
        print(f"  門檻: {int(threshold*100)}%")
        print(f"{'='*60}\n")

        cases = [
            # --- ⚠️ 風險警報（水位大幅下降）---
            ("風險警報：100% → 50% (大幅下降)",     1.0,  0.50, "⚠️"),
            ("風險警報：80% → 60% (下降 20%)",      0.80, 0.60, "⚠️"),
            ("風險警報：55% → 30% (下降 25%)",      0.55, 0.30, "⚠️"),
            ("風險警報：100% → 90% (剛好 10%)",     1.0,  0.90, "⚠️"),

            # --- ❌ 不應觸發（微幅下降）---
            ("不觸發：100% → 95% (僅降 5%)",        1.0,  0.95, "NONE"),
            ("不觸發：80% → 75% (僅降 5%)",         0.80, 0.75, "NONE"),
            ("不觸發：50% → 42% (降 8%，未達門檻)", 0.50, 0.42, "NONE"),

            # --- ✅ 警報解除（水位重回 100%）---
            ("警報解除：50% → 100% (V轉滿血)",      0.50, 1.0,  "✅"),
            ("警報解除：90% → 100% (微升回滿)",      0.90, 1.0,  "✅"),
            ("警報解除：95% → 100% (差一步回滿)",    0.95, 1.0,  "✅"),
            ("警報解除：30% → 100% (觸底反彈)",      0.30, 1.0,  "✅"),

            # --- ❌ 不應觸發（已經在 100%）---
            ("不觸發：100% → 100% (持平滿水位)",     1.0,  1.0,  "NONE"),

            # --- 📈 風險緩解（大幅好轉但未滿）---
            ("風險緩解：30% → 55% (升 25%)",         0.30, 0.55, "📈"),
            ("風險緩解：50% → 70% (升 20%)",         0.50, 0.70, "📈"),
            ("風險緩解：40% → 50% (剛好 10%)",       0.40, 0.50, "📈"),
            ("風險緩解：60% → 90% (升 30%)",         0.60, 0.90, "📈"),

            # --- ❌ 不應觸發（微幅回升）---
            ("不觸發：50% → 55% (僅升 5%)",          0.50, 0.55, "NONE"),
            ("不觸發：70% → 75% (僅升 5%)",          0.70, 0.75, "NONE"),
            ("不觸發：80% → 85% (僅升 5%)",          0.80, 0.85, "NONE"),

            # --- 邊界：首次執行 ---
            ("首次執行：無舊狀態 → 55%",             None, 0.55, "NONE"),
            ("首次執行：無舊狀態 → 100%",            None, 1.0,  "NONE"),

            # --- 邊界：持平 ---
            ("不觸發：50% → 50% (完全持平)",         0.50, 0.50, "NONE"),
            ("不觸發：70% → 70% (完全持平)",         0.70, 0.70, "NONE"),
        ]

        for label, old, new, expected in cases:
            total += 1
            if run_case(label, market_label, market_key, old, new, expected):
                passed += 1

    # 彙總
    print(f"\n{'='*60}")
    print(f"  回測結果：{passed}/{total} 通過")
    if passed == total:
        print(f"  🎉 全部通過！邏輯完全正確！")
    else:
        print(f"  ⚠️ 有 {total - passed} 個測試失敗，請檢查邏輯！")
    print(f"{'='*60}\n")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
