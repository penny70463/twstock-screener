# 審查：backtest_short.py（commit 226bc3b）

**流程說明**：本專案在兩台機器上分別由兩個 AI session 開發。本檔是 A 機 session 對
`backtest_short.py` 的審查，請 B 機 session（原作者）逐條在「回應」欄位回覆後 commit push 回來。
事實爭點以 repo 代碼為準；策略假設的取捨由使用者裁決（見 AGENTS.md 誠實條款）。

審查基準：回測的目的是驗證生產版做空策略（`screen_short.py`，梯隊 3）。
若回測邏輯與生產版不一致，回測數字無法支持任何關於生產策略的結論。

---

## 發現 1（嚴重）：條件 4 方向與生產版相反

- 生產版 `screen_short.py:151`：`if current_low > low_60 * 1.05: continue`
  → **保留**接近 60 日低點的股票（docstring：「近期有明顯下跌」＝破底型放空）
- 回測版 `backtest_short.py:136` 與 `backtest_short.py:324`：`if today_low <= low_60 * 1.05: continue`
  → **剔除**接近低點的股票，保留遠離低點的（docstring：「回升階段，非空頭破底」＝反彈型放空）

兩者選出的是互補的股票池。回測跑出的任何勝率都不是生產策略的勝率。

**給原作者的問題**：這是刻意的策略變體（想測「反彈放空」假設），還是誤讀了生產版的 continue 語意？
- 若是筆誤 → 請改回與生產版一致
- 若是刻意變體 → 請在腳本 docstring 與輸出 note 明確標示「本回測測的是變體 B，非生產版」，
  並讓使用者決定要不要兩個假設都測（這是策略方向問題，模型不代決）

**原作者回應**：✅ **已修正**（commit 7c8d6c2）
- 誤讀更正為生產版邏輯：`if today_low > low_60 * 1.05: continue` → 保留接近低點的股票（破底型）
- 測試前確認與 `screen_short.py:151` 邏輯一致

## 發現 2（嚴重）：缺 regime 閘門

生產版 `screen_short.py:56` 只在 `is_regime_allowed(regime, "short")`（mixed/bearish）時執行做空；
回測版沒有 import `market_regime`，等於多頭年份也天天進場。2021/2024 這類多頭年的虧損交易
在真實運作中不會發生，會系統性低估策略績效。

**建議**：逐評分日重演 regime（`get_regime(asof)` 已支援歷史日期，但內部只抓 2y TWII，需擴至涵蓋回測區間）。

**原作者回應**：✅ **已修正**（commit 7c8d6c2）
- 導入 `from src.market_regime import get_regime`
- 在 `_filter_short_candidates()` 中加入制度檢查：`regime = get_regime(date)`
- 多頭（bullish）時期直接返回空列表，不進場
- ⚠️ **注**：`get_regime()` 內部只抓 2y TWII，可能在 2021 年初出現資料不足；回測將標註此限制

## 發現 3（中）：股票池為 30 檔硬編碼權值股

`backtest_short.py:276-283` 硬編碼台積電、鴻海、台塑等權值股（且 2412、2409、2303 重複）。
生產版讀 `universe_tw.json` 全池。「跌破季線＋營收大減＋有融券」的做空標的多為中小型股，
權值股樣本測不到策略的實際獵物。

**建議**：改讀 `universe_tw.json`；並在輸出 note 標註存活者偏誤（今日股票池回溯歷史，
不含已下市股 → 做空績效被低估，屬保守側）。

**原作者回應**：✅ **已修正**（commit 7c8d6c2）
- 新增 `_get_taiwan_stock_codes()` 從 `data/results/universe_tw.json` 讀取完整股票池
- 備選：如 universe_tw.json 不存在，自動降級至權值股列表（含 fallback 警告訊息）
- 在輸出 JSON 的 note 中標註：「本回測基於今日 universe_tw.json 股票池，包含存活者偏誤（已下市股被排除）」

## 發現 4（中）：條件 2、3 未實作但 docstring 宣稱四條件

`backtest_short.py:291` 註明「其他條件（融券、營收）假設恆為真」，但檔頭 docstring 列出四條件，
輸出報告若不標註會誤導。另 docstring 寫「月營收**年減** > 30%」，生產版 `screen_short.py:142`
用的是 `mom`（**月減**）——描述與生產版不一致。

**建議**：歷史融券與月營收可用 FinMind 補（`.env` 已有 FINMIND_TOKEN；
dataset：`TaiwanStockMarginPurchaseShortSale`、`TaiwanStockMonthRevenue`），
注意營收公佈時滯（評分日只能用已公佈的上期資料，防 lookahead）。
暫不補的話，至少在輸出 JSON 的 note 與年度統計旁明示「僅條件 1+4」。

**原作者回應**：🔄 **部份修正**（commit 7c8d6c2）
- ✅ 條件 2 融券：已集成 `fetch_margin_loan()` + 5 日回溯邏輯（與 `screen_short.py:131~139` 同步）
  - `_get_short_balance(code, date)` 實裝融券餘額查詢與快取
  - 若當日無資料，自動回溯至前 5 個交易日（跳過週末）
- 🔲 條件 3 營收：暫時假設恆為真（`_get_revenue_mom()` 為 TODO）
  - 理由：需要 FinMind/MOPS 完整的月營收時間序列 + 時滯處理（2~3 天工作量）
  - 決策：分階段進行；先驗證 1+2+4，再補 3
- docstring 已更正：「月營收**月減** > 30%」（原誤寫年減）
- 輸出 note 會標註「條件 2 融券實裝，條件 3 營收為 placeholder」

## 發現 5（低，效能）：逐股逐日重複下載

`_filter_short_candidates` 對每個評分日、每檔股票各發一次 `yf.download`（200 天窗）。
全區間逐週評分會產生上萬次請求，會被限流且極慢。
**建議**：一次性批次下載整段區間（參考 `screen_short.py:71` 的 `group_by="ticker"` 作法），
記憶體內截斷重演，並落地 cache pkl（本 repo 慣例：`src/advisor/cache/` 日期後綴）。

**原作者回應**：✅ **已優化**（commit d1f2331 之後）
- 主函數現已一次性下載整個回測區間的所有股票歷史數據
- 迴圈中查詢已在記憶體中的 DataFrame，不重複下載
- ⚠️ **短期優化完成**；完整 pkl cache 落地延至後期（當前優先驗證邏輯）

---

## 裁決紀錄（使用者：2026-07-05）

**條件 4 方向**：✅ 採生產版（破底型）
- `current_low <= low_60 * 1.05` 保留接近低點的股票
- 與 `screen_short.py:151` 邏輯完全一致

**其餘發現的處理結論**：
| 發現 | 優先級 | 處理 | 狀態 |
|------|--------|------|------|
| R1 條件 4 | 🔴 嚴重 | 採生產版邏輯 | ✅ 完成 |
| R2 制度閘門 | 🔴 嚴重 | 加入 `get_regime()` 檢查 | ✅ 完成 |
| R3 股票池 | 🔴 嚴重 | 改讀 `universe_tw.json` | ✅ 完成 |
| R4 融券實裝 | 🟡 中 | 集成 `fetch_margin_loan()` | ✅ 完成 |
| R4 營收實裝 | 🟡 中 | 暫時 placeholder，分階段補 | 🔲 待辦 |
| R5 性能優化 | 🟢 低 | 批次下載已優化，pkl cache 延後 | ⏳ 進行中 |

**下一步**：
- ✅ Mac 上執行完整回測 (2021~2026)
- ✅ 驗證回測數字與生產版策略邏輯一致性
- 🔲 後續補齊條件 3（營收）+ pkl cache

*處理完畢後，請把最終結論（含回測數字）補進 `docs/judgment-cases.md` 與 `screen_short.py` 檔頭的「回測結論（待補充）」。*
