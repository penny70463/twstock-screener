# 台股動能與題材掃描儀表板 (twstock-screener)

結合長線技術指標、法人籌碼與短線動能的全自動化多因子選股系統，並使用 LLM 進行每日題材族群分類。

## 系統架構（前後端分離）

本專案採用完美的「前後端分離」與「靜態託管」架構，以達到零成本、零維護、載入秒開的目標：

```text
[Mac 本機 Cron 排程] ──跑管線──> data/results/latest.json ──Push──> [GitHub] ──Fetch──> [Vercel 靜態前端]
```

- **後端 (Python)**：
  - 每天下午 18:00 自動在 Mac 背景執行 `run_daily.sh`。
  - 呼叫 `src/pipeline.py` 串接強大的 Advisor 核心邏輯（整合大盤多空門檻、均線多頭排列、三大法人籌碼）。
  - 取出符合長線保護條件且「當日漲幅強勢」的標的，送交 NVIDIA LLM 分類題材。
  - 將結果存為 JSON 檔並自動 Git Commit & Push 到 GitHub。
- **前端 (Vue 3 + Vite)**：
  - 位於 `frontend/` 目錄，部署於 Vercel。
  - 使用者開啟網頁時，瀏覽器會直接從 GitHub Raw 抓取最新的 `latest.json` 進行渲染，速度極快且無需後端伺服器。

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
# 每個交易日 (週一至週五) 的 18:00 執行
0 18 * * 1-5 /你的絕對路徑/twstock-screener/run_daily.sh >> /你的絕對路徑/twstock-screener/cron_log.txt 2>&1
```
