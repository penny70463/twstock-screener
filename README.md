# 台股每日強勢股篩選 + 題材族群分類

每日篩出**漲幅排行前 N 名**且**站上 20/60/120/240 日均線**的個股，再用 LLM 依**投資題材**分群並說明。

## 架構（為何這樣拆）

資料管線是「批次、慢、每天跑一次」，網頁是「讀結果、即時顯示」——兩者性質不同，所以拆開：

```
GitHub Actions (每日排程) ──跑管線──> data/results/*.json ──讀──> Streamlit 網頁
```

- **管線**：`run_pipeline.py` → TWSE 全上市當日行情排漲幅前 N → 對 top-N 用 FinMind 單股抓 240 日歷史算四均線 → NVIDIA LLM 分題材 → 存 JSON。
- **網頁**：`app.py` 只讀 `data/results/latest.json` 顯示，不現抓現算（快又省 API）。
- **LLM**：走 NVIDIA NIM（OpenAI 相容雲端 API），本地**不跑模型**、不吃 GPU，venv 只裝 `openai` 套件發請求。

```
twstock-screener/
├── config.py            # env 與篩選參數
├── run_pipeline.py      # 排程入口
├── app.py               # Streamlit 顯示
├── src/
│   ├── data_source.py   # TWSE 當日全市場 + FinMind 單股歷史 + parquet 快取
│   ├── ranking.py       # 漲幅前 N
│   ├── ma_filter.py     # 四均線篩選
│   ├── classifier.py    # NVIDIA LLM 題材分類
│   ├── prompts.py       # 分類 system prompt
│   └── pipeline.py      # 串接
└── .github/workflows/daily.yml
```

## 本地執行

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # 填入 NVIDIA_API_KEY（與 nvidia-tg-bot 同一把）

python run_pipeline.py      # 產生 data/results/latest.json
streamlit run app.py        # 開網頁看結果
```

只想篩選、先不花 LLM 額度：`python run_pipeline.py --no-llm`。

## 參數（.env）

| 變數 | 說明 | 預設 |
|---|---|---|
| `NVIDIA_API_KEY` | NVIDIA NIM 金鑰（必填才會分類） | — |
| `NVIDIA_BASE_URL` | NIM 端點 | `https://integrate.api.nvidia.com/v1` |
| `NVIDIA_MODEL` | 模型 | `meta/llama-3.1-70b-instruct` |
| `FINMIND_TOKEN` | FinMind token，提高額度（建議填） | 空 |
| `TOP_N` | 漲幅取前幾名 | `200` |
| `MA_WINDOWS` | 均線天數 | `20,60,120,240` |

## 部署

### GitHub Actions 排程（免費）
推上 GitHub 後，到 repo **Settings → Secrets and variables → Actions** 設定 `NVIDIA_API_KEY`、`FINMIND_TOKEN` 等。
workflow 每個交易日台灣時間 15:00 自動跑，產出結果 commit 回 repo。也可在 Actions 頁手動 `Run workflow`。

### Streamlit Community Cloud（免費）
1. 連結此 repo，主程式選 `app.py`。
2. 金鑰**不要寫進程式碼**，到 App → Settings → **Secrets** 填入（TOML 格式）：
   ```toml
   NVIDIA_API_KEY = "xxx"
   FINMIND_TOKEN = "xxx"
   ```
3. 網頁只讀 `data/results/`，由 Actions 排程更新；公開 app 也安全（金鑰在 Secrets，不進前端）。

## 注意事項

- **資料範圍：目前僅上市（TWSE）**。漲幅排行用 TWSE 官方 OpenAPI（免費、免 auth、一次拿全上市）。上櫃（TPEx）因 OpenAPI 憑證在 Python 3.13 驗證失敗，暫未納入。
- **為何不用 FinMind 全市場**：FinMind 的 `TaiwanStockPrice` 全市場查詢需付費等級（Backer/Sponsor）；免費等級只能帶 `data_id` 查單股。故均線歷史改對 top-N 逐檔抓（200 檔 < 600/hr）。
- **FinMind 流量與快取**：免 token 300/hr、有 token 600/hr。逐檔歷史用 parquet 快取，之後每天只增量抓最新一天。若觸發 rate limit（402），已快取的股票會自動降級沿用快取，不中斷整批。**建議填 `FINMIND_TOKEN`** 並避免短時間重複跑。
- **題材分類品質瓶頸在「公司業務描述」**：目前只餵官方產業別，LLM 對冷門中小型題材股可能判得不準。要更準可改餵公司主營業務描述，或加掛人工維護的概念股對照表。
- **均線資料不足**：上市未滿 240 個交易日者，無法確認 240 日均線，一律視為「未站上」排除。
