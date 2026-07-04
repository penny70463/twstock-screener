#!/bin/bash
# scripts/verify_done.sh — Definition of Done 機械化檢查
#
# 用途：AI 代理或開發者在宣告「完成」前必跑。任何 FAIL 都不得宣告完成，
#       只能如實回報「做到哪、還缺什麼」。詳細判準見 AGENTS.md。
#
# 用法：
#   scripts/verify_done.sh
#   BACKTEST_EVIDENCE=/path/to/backtest_output.txt scripts/verify_done.sh
#       （改了 src/advisor/config.py 時，必須提供回測輸出檔證據）
#   SKIP_DRY_RUN=1 scripts/verify_done.sh
#       （離線環境跳過 etf_alert --dry-run，會降級為 WARN 並要求補驗）
set -u
cd "$(git rev-parse --show-toplevel)"

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

PASS=0; FAIL=0; WARN=0
pass() { echo "  [PASS] $1"; PASS=$((PASS+1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }
warn() { echo "  [WARN] $1"; WARN=$((WARN+1)); }

# ---- 收集改動檔案（相對 HEAD 的已改 + 未追蹤，排除資料與快取）----
changed=$( (git diff --name-only HEAD; git ls-files --others --exclude-standard) \
  | sort -u | grep -vE '^(\.venv/|data/|src/advisor/cache/)' | grep -vE '\.(pkl|json|csv)$' || true)
changed_py=$(echo "$changed" | grep '\.py$' || true)

echo "=== verify_done：Definition of Done 檢查 ==="
echo "改動檔案（不含資料/快取）："
if [ -n "$changed" ]; then echo "$changed" | sed 's/^/  - /'; else echo "  （無）"; fi
echo

# ---- 1. Python 語法底線（無 IDE 診斷時的最低標準）----
echo "[1] Python 語法檢查"
if [ -z "$changed_py" ]; then
  pass "沒有改動 .py 檔，跳過"
else
  syntax_ok=1
  while IFS= read -r f; do
    [ -f "$f" ] || continue
    if ! "$PY" -m py_compile "$f" 2>/tmp/verify_syntax_err; then
      fail "$f 語法錯誤：$(tail -1 /tmp/verify_syntax_err)"
      syntax_ok=0
    fi
  done <<< "$changed_py"
  [ $syntax_ok -eq 1 ] && pass "改動的 .py 檔語法皆通過"
fi
echo

# ---- 2. data/results/ 手改防護 ----
echo "[2] data/results/ 手改防護"
results_dirty=$(git diff --name-only HEAD -- 'data/results/' || true)
if [ -n "$results_dirty" ] && [ "${PIPELINE_COMMIT:-0}" != "1" ]; then
  fail "偵測到 data/results/ 有未 commit 的改動——結果檔只能由管線產生並自動 commit，不得手改：$(echo "$results_dirty" | tr '\n' ' ')"
else
  pass "data/results/ 無手動改動"
fi
echo

# ---- 3. 離線測試（改到核心邏輯就跑，全程不連網、秒級完成）----
echo "[3] 離線測試"
core_changed=$(echo "$changed" | grep -E '^(src/|etf_alert\.py|run_pipeline\.py|eval_tw\.py)' || true)
if [ -z "$core_changed" ]; then
  pass "沒有改動核心邏輯，跳過"
else
  if out=$("$PY" tests/test_exposure_alerts.py 2>&1); then
    pass "tests/test_exposure_alerts.py 通過"
  else
    fail "tests/test_exposure_alerts.py 失敗（節錄）：$(echo "$out" | tail -3 | tr '\n' ' ')"
  fi
  if "$PY" -c "import pytest" 2>/dev/null; then
    if out=$("$PY" -m pytest tests/test_us_screener.py -q 2>&1); then
      pass "tests/test_us_screener.py 通過"
    else
      fail "tests/test_us_screener.py 失敗（節錄）：$(echo "$out" | tail -3 | tr '\n' ' ')"
    fi
  else
    warn "pytest 未安裝（.venv/bin/pip install pytest），test_us_screener.py 未執行"
  fi
fi
echo

# ---- 4. 策略參數改動必須附回測證據 ----
echo "[4] config.py 回測證據"
if echo "$changed" | grep -q '^src/advisor/config\.py$'; then
  if [ -n "${BACKTEST_EVIDENCE:-}" ] && [ -s "${BACKTEST_EVIDENCE}" ]; then
    pass "回測證據：${BACKTEST_EVIDENCE}（$(wc -l < "$BACKTEST_EVIDENCE" | tr -d ' ') 行）——回報時必須附上前後對比數字"
  else
    fail "src/advisor/config.py 有改動但沒有回測證據。請跑對應回測並把輸出存檔，再以 BACKTEST_EVIDENCE=<檔案> 重跑本腳本。候選：tests/backtest_tw_ranking.py、tests/backtest_exposure_3y.py --compare、tests/backtest_kd_per_etf.py"
  fi
else
  pass "config.py 未改動，跳過"
fi
echo

# ---- 5. 警報邏輯改動必須 dry-run（絕不真發 LINE）----
echo "[5] etf_alert dry-run"
if echo "$changed" | grep -q '^etf_alert\.py$'; then
  if [ "${SKIP_DRY_RUN:-0}" = "1" ]; then
    warn "etf_alert.py 有改動但以 SKIP_DRY_RUN=1 跳過 dry-run——回報時必須明列此項未驗證"
  elif out=$("$PY" etf_alert.py --dry-run 2>&1); then
    pass "etf_alert.py --dry-run 執行成功"
  else
    fail "etf_alert.py --dry-run 失敗（節錄）：$(echo "$out" | tail -3 | tr '\n' ' ')"
  fi
else
  pass "etf_alert.py 未改動，跳過"
fi
echo

# ---- 總結 ----
echo "=== 結果：PASS=$PASS FAIL=$FAIL WARN=$WARN ==="
if [ $FAIL -gt 0 ]; then
  echo "有 FAIL：不得宣告完成，只能回報「做到哪、還缺什麼」。"
  exit 1
fi
if [ $WARN -gt 0 ]; then
  echo "全數通過但有 WARN：宣告完成時必須明列 WARN 項目為「未驗證」。"
fi
exit 0
