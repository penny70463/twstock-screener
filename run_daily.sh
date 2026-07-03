#!/bin/bash
# 設定出現錯誤就停止執行
set -e

# 切換到專案目錄
cd /Users/penny/twstock-screener

# 為了確保 cron 執行時環境變數正確，設定基礎 PATH
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# 啟動虛擬環境
source .venv/bin/activate

# 若執行過程中發生錯誤，則觸發錯誤通知
trap 'python notify_error.py' ERR

echo "========================================"
echo "執行時間：$(date)"
echo "開始執行股票篩選..."

# 執行主要的 Python 腳本
# (Python 會自動去讀取資料夾底下的 .env 檔案)
# Line 推播改由最後的 send_daily_line.py 統一發送（合併各選股腳本結果）
python -u run_pipeline.py --no-line

echo "執行 ETF 警報系統..."
python -u etf_alert.py

echo "執行 ETF 每月體檢 (內部自動判斷日期)..."
python -u etf_monthly_review.py

echo "執行回檔轉強篩選 (screen_pullback.py)..."
# 放在 if 內：失敗（如觀測站限流）只提示，不中斷主要排程與推播
if ! python -u screen_pullback.py; then
  echo "回檔轉強篩選失敗，跳過（不影響其他結果）"
fi

echo "執行族群出量突破偵測 (screen_breakout.py)..."
if ! python -u screen_breakout.py; then
  echo "族群突破偵測失敗，跳過（不影響其他結果）"
fi

echo "統一發送每日 Line 訊息..."
if ! python -u send_daily_line.py; then
  echo "Line 發送失敗，跳過（不影響結果推送）"
fi

echo "執行完畢，準備將結果推播到 GitHub..."

# 以下照搬 GitHub Actions 裡面的自動 Push 邏輯
git add -f data/results/*.json
git add -f data/results/screen_pullback_result_*.csv 2>/dev/null || true
if git diff --cached --quiet; then
  echo "沒有新結果，跳過 Push。"
  exit 0
fi

git commit -m "chore: daily screener result $(date +%F) (from local cron)"

# 排程執行期間遠端可能被其他 push 更新，先 rebase 再推，重試防競態
for i in 1 2 3; do
  git pull --rebase --autostash origin master \
    && git push origin master \
    && echo "Push 成功！" \
    && exit 0
  echo "push 衝突，重試 $i ..."
  sleep 5
done

echo "push 連續失敗！" && exit 1
