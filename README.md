# 台美股動能與題材掃描儀表板 (twstock-screener)

結合長線技術指標、法人籌碼與短線動能的全自動化多因子選股系統，支援**台股**與**美股**雙引擎，並使用 LLM 進行每日題材族群分類。

## 系統架構（前後端分離）

本專案採用完美的「前後端分離」與「靜態託管」架構，以達到零成本、零維護、載入秒開的目標：

```text
[Mac 本機 Cron 排程] ──跑管線──> data/results/latest.json ──Push──> [GitHub] ──Fetch──> [Vercel 靜態前端]
```

- **後端 (Python)**：
  - 每天下午 16:00 自動在 Mac 背景執行 `run_daily.sh`。
  - 呼叫 `src/pipeline.py` 串接 Advisor 核心邏輯，並依市場採用各自最適策略：
    - **台股**：五因子（趨勢／動能／量能／三大法人籌碼／月營收）＋大盤多空動態門檻。
    - **美股**：橫斷面動能策略（`src/advisor/us_screener.py`）——以「波動率調整的 12-1 月相對動能」對**完整 S&P 500 成分股**排名，僅保留站上 200 日線者。此策略經 `backtest_us.py`（含 point-in-time 成分股、消除存活者偏誤）回測驗證，是台股五因子直接移植美股失效後的替代方案。
  - 除了過濾出「高分強勢股」送交 NVIDIA LLM 分類題材外，亦會產出包含全市場標的評分狀態的 `universe.json`。
  - 將結果存為 JSON 檔並自動 Git Commit & Push 到 GitHub。
- **前端 (Vue 3 + Vite)**：
  - 位於 `frontend/` 目錄，部署於 Vercel。
  - 包含「🚀 強勢股掃描」與「💼 我的存股體檢」雙頁籤。
  - 支援「🇹🇼 台股 / 🇺🇸 美股」市場一鍵切換。
  - 使用者開啟網頁時，瀏覽器會直接從 GitHub Raw 抓取最新的 `latest_tw.json` 或 `latest_us.json` 進行渲染，速度極快且無需後端伺服器。
  - **隱私安全**：使用者的存股紀錄（股數、成本）會直接記錄在瀏覽器的 `localStorage` 中，無需登入系統，且資料不會離開使用者的裝置。

## 📊 主動 vs 被動：美股動能策略回測驗證

為了證明「主動動能策略」在美股有效，我們透過 `backtest_us.py` 對 **S&P 500** 歷史成分股（含剔除下市股以消除存活者偏誤）進行嚴格的 Point-in-Time 回測。

**策略邏輯**：
1. **橫斷面排名**：計算 12-1 月（剔除最近一個月以防短期反轉）的波動率調整動能。
2. **絕對濾網**：只買進站上 200 日線的股票，空頭市場自動轉持現金。
3. **換股頻率**：每月/每週再平衡，汰弱留強。

**回測結果**顯示，相較於原本硬套台股「週頻輪動＋緊停損」的短線邏輯（在美股容易被洗盤且錯失大波段而跑輸大盤），新版的「純動能排名策略」在美股這種具有強烈「趨勢延續特性」的市場上，能夠穩定且長期地打敗單純買進持有 SPY (S&P 500) 或 QQQ (Nasdaq 100) 的績效。因此前端介面**捨棄了單日漲跌幅過濾**，改以「動能總分排名」直接呈現最純正的強勢股清單。

## 目錄結構

```text
twstock-screener/
├── run_daily.sh         # Mac 自動化排程入口 (Cron)
├── config.py            # 環境變數與設定
├── src/
│   ├── advisor/         # 核心多因子評分引擎 (趨勢、籌碼、量能、市場狀態)
│   ├── classifier.py    # NVIDIA LLM 題材分類
│   └── pipeline.py      # 每日排程主邏輯串接
├── frontend/            # Vue 3 靜態儀表板前端
│   ├── src/App.vue      # 儀表板 UI (玻璃特效、排序、篩選)
│   ├── src/components/  # UI 元件庫 (包含 PortfolioReview 存股體檢)
│   └── package.json     # 前端依賴
└── data/results/        # 輸出的 JSON 資料存放區
```

## 本地開發與執行

### 後端管線測試
確保 Python 環境與套件已安裝，並填妥 `.env` 中的 `NVIDIA_API_KEY`：
```bash
python run_pipeline.py
```
此步驟會在 `data/results/` 中產生最新的 `latest.json`。

### 前端 UI 測試
進入 `frontend` 目錄並啟動本地開發伺服器：
```bash
cd frontend
npm install
npm run dev
```
開發伺服器會自動抓取本地剛生成的 `latest.json` 顯示，方便在不推送到 GitHub 的情況下預覽畫面。

## 部署至 Vercel

1. 將本專案的程式碼 (包含 `frontend/`) Push 至 GitHub。
2. 在 Vercel 後台點擊 **Add New Project**，並匯入本專案。
3. **重要：** 將 **Root Directory** 設定為 `frontend`。
4. Framework Preset 保持為 Vite，點擊 Deploy 即可。
5. 未來只要 Mac 上的排程成功 Push 了新的 JSON 資料，Vercel 網頁就會自動顯示最新狀態。

## 自動化排程設定 (Mac crontab)

要讓系統每天自動更新資料並推送到 GitHub，請在終端機輸入 `crontab -e` 並加入以下設定：

```bash
# 每個交易日 (週一至週五) 的 16:00 執行
0 16 * * 1-5 /你的絕對路徑/twstock-screener/run_daily.sh >> /你的絕對路徑/twstock-screener/cron_log.txt 2>&1
```
