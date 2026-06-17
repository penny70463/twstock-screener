#!/bin/bash
# 設定出現錯誤就停止執行
set -e

# 切換到專案目錄
cd /Users/penny/twstock-screener

# 為了確保 cron 執行時環境變數正確，設定基礎 PATH
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# 啟動虛擬環境
source .venv/bin/activate

echo "========================================"
echo "執行時間：$(date)"
echo "開始執行股票篩選..."

# 執行主要的 Python 腳本
# (Python 會自動去讀取資料夾底下的 .env 檔案)
python -u run_pipeline.py

echo "執行完畢，準備將結果推播到 GitHub..."

# 以下照搬 GitHub Actions 裡面的自動 Push 邏輯
git add -f data/results/*.json
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
