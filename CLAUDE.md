# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 行為準則（必讀）

@AGENTS.md

上述 AGENTS.md 是跨工具共用的判斷與驗證準則（Definition of Done、何時停下來問、方向錯誤訊號、驗證紀律），與本檔的技術說明互補，兩者都必須遵守。

## 常用命令

### 後端管線
```bash
# 執行每日篩選管線（產生 data/results/latest_tw.json 與 latest_us.json）
python run_pipeline.py

# 測試 ETF 警報邏輯（不發送 LINE）
python etf_alert.py --dry-run

# 執行每月策略回測驗證
python etf_monthly_review.py
```

### 前端開發
```bash
cd frontend && npm install && npm run dev    # 開發伺服器 http://localhost:5173
cd frontend && npm run build                  # 生產建置
```

### 回測與測試
```bash
python tests/backtest_exposure_3y.py --compare    # 3 年曝險模型回測
python tests/test_us_screener.py                  # 美股篩選器測試
```

## 高階架構

### 每日管線流程
```
run_daily.sh (cron 16:00)
  └─ python run_pipeline.py
       ├─ src/pipeline.py → 串接 advisor 核心邏輯
       │   ├─ TW: 五因子評分 (screener.py) + 大盤多空門檻
       │   └─ US: 純動能排名 (us_screener.py)
       ├─ src/classifier.py → NVIDIA LLM 題材分類
       └─ 輸出 JSON → git push
  └─ python etf_alert.py → LINE 警報推播
```

### 市場策略差異
| 維度 | 台股 (TW) | 美股 (US) |
|------|-----------|-----------|
| 策略 | 五因子評分 (趨勢30/動能35/量能15/籌碼10/營收10) | 波動率調整動能排名 |
| 門檻 | 依市場狀態動態調整 (70/75/80) | 僅過濾站上 200MA |
| 曝險目標 | VOL_TARGET_TW = 20% | VOL_TARGET_US = 15% |

### 曝險管理模型 (market.py)
連續型 3-factor 計算：`exposure = (0.5×trend + 0.5×breadth) × vol_scaling`
- trend = 價格>MA60 (0.4) + MA60>MA120 (0.3) + MA60斜率>0 (0.3)
- breadth = 市場中站上 60MA 的股票比例
- vol_scaling = min(1, target_vol / realized_vol)

## 關鍵模組

| 檔案 | 用途 |
|------|------|
| `src/advisor/config.py` | 所有可調參數 (70+ 設定)，修改需回測驗證 |
| `src/advisor/screener.py` | 台股五因子篩選與評分邏輯 |
| `src/advisor/us_screener.py` | 美股動能策略實作 |
| `src/advisor/market.py` | 市場狀態判斷與曝險水位計算 |
| `src/classifier.py` | NVIDIA LLM 題材分類 (批次 30 股/請求) |
| `etf_alert.py` | ETF 紅綠燈與 KD 警報 (LINE 推播) |

## 環境變數 (.env)

```bash
# 必要
NVIDIA_API_KEY=           # LLM 題材分類
LINE_CHANNEL_ACCESS_TOKEN= # LINE 警報推播
LINE_ALLOWED_USER_IDS=    # 接收警報的用戶 ID (逗號分隔)

# 選用
FINMIND_TOKEN=            # 台股法人籌碼增強資料
```

## 開發注意事項

- 修改 `src/advisor/config.py` 中的參數後，應執行 `tests/backtest_*.py` 驗證影響
- TWSE/TPEX API 有速率限制，程式碼已內建指數退避處理
- LLM 批次限制為 30 股/請求，以避免 CI 超時 (270s)
- 結果 JSON 會自動 git commit/push，避免手動修改 `data/results/` 內的檔案
- 前端 localStorage 儲存用戶存股資料，不會上傳
