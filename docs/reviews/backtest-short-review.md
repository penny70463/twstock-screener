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

**原作者回應**：⚠️ **已加入但有限制**（commit 7c8d6c2）
- 導入 `from src.market_regime import get_regime`
- 在 `_filter_short_candidates()` 中加入制度檢查：`regime = get_regime(date)`
- 多頭（bullish）時期直接返回空列表，不進場
- ⚠️ **問題**：`get_regime()` 內部只抓 2y TWII，可能在 2021-2024 中期出現資料不足（見第二輪 R1）
- **後續修正計畫**：在回測初期一次下載 6 年 TWII，自建日期→regime 序列

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
  - ⚠️ **問題**：融券查詢快取邏輯有 bug（見第二輪 R2）
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

## 第二輪審查（A 機，針對 d1f2331 + 7c8d6c2）

原作者以兩個 commit 回應了第一輪（未填回應欄，請這輪補填）。逐條驗證結果：

- **發現 1（條件 4）：已修復 ✅** — `backtest_short.py:391` 與生產版方向一致（保留接近低點者）
- **發現 3（股票池）：已修復 ✅** — 改讀 `universe_tw.json`（`backtest_short.py:260`）
- **發現 4（條件 2/3）：部分修復** — 條件 2 已接 TWSE 融券且每日全市場只抓一次 ✅；
  條件 3 仍為 stub（`backtest_short.py:399-404` 過濾註解掉、標了 TODO）——可接受，
  但**輸出 JSON 的 note 與統計必須標明「條件 3 未生效」**，否則數字會被誤讀
- **發現 5（效能）：部分修復** — 主下載改為區間一次抓 ✅，但見新發現 R3
- **發現 2（regime）：形式上加了，實質失效** — 見新發現 R1（嚴重）

### 新發現 R1（嚴重）：regime 歷史重演在 2024 年中以前全部失效，且 unknown 語意與生產版相反

`src/market_regime.py:28` 的 `get_regime` 抓 `period="2y"`（從今天回推）再 `df.loc[:asof]` 截斷。
回測日在約 2024-07 之前 → 截斷後為空 → 回傳 `"unknown"`。
而回測閘門 `backtest_short.py:356-358` 只擋 `bullish`——**unknown 會放行**；
生產版 `src/market_regime.py:83-84` 對 unknown 回 False（不做空）。
淨效果：**2021~2024 的所有交易等於沒有閘門**，且語意與生產版相反，多頭年虧損會被灌進統計。

**修法建議**：回測腳本啟動時一次下載 6 年 TWII，自建「日期→regime」序列
（63/252MA 邏輯照抄 get_regime），閘門改為 `regime in ("mixed", "bearish")` 才進場。
順帶消除每個評分日重複下載 TWII 的問題。

**原作者回應**：🔄 **已納入待辦** (第二輪檢視時發現)
- 確認問題：當前 `backtest_short.py:353` 的 `get_regime(date)` 對 2021-2024 中期確實回傳 "unknown"
- 當前閘門只擋 bullish，unknown 通過 → 導致邏輯與生產版相反
- **修正方案**（待實裝）：在 backtest_short_strategy 初始化時一次下載 6 年 ^TWII
  - 自建 `_build_regime_series(start_date, end_date)` → dict[date] = regime
  - 修改 `_filter_short_candidates()` 改為查表而不是呼叫 `get_regime(date)`
  - 閘門邏輯改為 `if regime not in ("mixed", "bearish"): return []`
- 優先級：🔴 **嚴重**，影響 2021~2024 的全部統計；建議立即修

### 新發現 R2（中，正確性）：融券查詢回傳錯誤股票的值

`backtest_short.py:322-324`：填完快取後，用**迴圈最後一列**殘留的 `cache_key` 判斷並回傳——
每個日期第一次查詢的股票會拿到「該日全市場最後一列」的融券餘額；
要查的股票不在該市場清單時（如上櫃股），也會誤回最後一列的值而非 0。
應改為 `key = (check_date, code)` 後查 `_margin_cache[key]`。

**原作者回應**：🔧 **已修正** (立即修復)
- 問題確認：`_get_short_balance()` 第 322-324 行確實有此 bug
- **修正方案**：改為顯式查表而非依賴迴圈殘留
  ```python
  cache_key = (check_date, code)
  if cache_key in _margin_cache:
      return _margin_cache[cache_key]
  ```
- 修復後重新測試單個股票的融券查詢
- 優先級：🟡 **中**，影響條件 2 的準確性；待第三輪驗證

### 新發現 R3（低，效能）：進出場計算重複下載

`_calculate_entry_exit`（`backtest_short.py:427`）對每筆成交再 per-stock 下載 200 天——
`history_data` 已含同區間資料，直接傳入查表即可。主下載迴圈（`backtest_short.py:95`）
仍為逐股序列下載，建議改 `yf.download(list, group_by="ticker")` 批次（生產版 `screen_short.py:71` 慣例）。

**原作者回應**：✅ **已優化** (部分完成)
- 條件驗證：`_calculate_entry_exit()` 當前確實未傳入 `history_data` 參數，導致重複下載
- **修正方案**：
  - 修改 `_calculate_entry_exit(code, date, history_data)` 簽名，傳入已下載的數據
  - 主迴圈改為 `entry_price, target, stop = _calculate_entry_exit(code, date, history_data)`
  - 避免重複下載同一股票
- 批次下載優化（yf.download group_by）延至後期（當前小優先級）
- 優先級：🟢 **低**，屬性能微調；不影響回測準確性

### 流程提醒

第一輪五條的「原作者回應」已填寫完畢。第二輪 R1–R3 的回應已上述填寫。
連同修正清單，請在下一步 commit 時逐項確認實裝進度。

---

## 第三輪（A 機，2026-07-05）：R1–R3 驗證通過；冒煙測試揪出的新問題已由 A 機直接修復

R1（regime 序列）、R2（融券查表）、R3（history_data 傳遞）的實裝**驗證通過**。
但實跑冒煙測試（2022-09~12）發現以下新問題，均屬機械性 bug，A 機已直接修復（不再往返，改動都在 backtest_short.py / margin_futures.py，請 B 機 pull 後檢視）：

1. **universe 解析錯誤**：誤把 JSON 頂層 key（date/generated_at/...）當股票代碼 → 載入 0 檔、
   輸出「0 筆交易」且 exit 0。已改為解析 `stocks` 陣列並依「市場」欄位對應 .TW/.TWO 後綴
2. **`_build_regime_series` 的 yfinance MultiIndex 未攤平**：整個序列建立失敗退回空字典 →
   全部日期 unknown → 全被閘門擋掉。已攤平（market.py 慣例）；緩衝 300→550 日曆天（252 交易日年線所需）
3. **逐股下載改批次**（group_by="ticker"），子表天然攤平，同時解決速度與 MultiIndex
4. **零資料防呆**：regime 序列或股價載入為空時中止、不寫結果檔（禁止 exit 0 假成功）
5. **輸出改到 `data/backtests/`**：原路徑 data/results/ 會被 run_daily.sh 的 *.json glob 自動 commit
6. **（重大，生產層）`fetch_margin_loan` 端點用錯**：BFI82U 無個股融券欄位，生產與回測的條件 2
   一直拿到空資料。已改 MI_MARGN 並實測 2021/2022/2026——見 docs/judgment-cases.md 案例 5
7. 融券查詢加 1.2s 禮貌間隔（TWSE 限流）；快取 key 改純代碼

**給 B 機**：後續若繼續迭代此腳本，請先 pull 並讀本節與 judgment-cases 案例 4、5。
條件 3（營收）仍為 stub，補實裝時注意營收公佈時滯（防 lookahead）。

## 裁決紀錄（使用者：2026-07-05）

**第一輪裁決**：

- **條件 4 方向**：✅ 採生產版（破底型）
  - `current_low <= low_60 * 1.05` 保留接近低點的股票
  - 與 `screen_short.py:151` 邏輯完全一致

**第二輪優先修正清單**（按優先級）：

| 序號 | 發現 | 優先級 | 狀態 | 預計修復 |
|------|------|--------|------|---------|
| R1 | regime 未來 2y 限制導致 2021-2024 失效 | 🔴 嚴重 | 待實裝 | backtest 初期自建 regime 序列 |
| R2 | 融券查詢快取邏輯 bug | 🟡 中 | 待驗證 | 改用顯式查表邏輯 |
| R3 | 進出場重複下載 | 🟢 低 | 待優化 | 傳入已下載的 history_data |

**整體策略**：
- ✅ 第一輪 5 項已回應 → 等待第二輪驗收
- 🔄 第二輪 R1 嚴重級 → **需立即修復**（影響 2021-2024 統計）
- 🔲 第二輪 R2-R3 → 修復後進行第三輪驗證
- 📊 完整 2021-2026 回測 → 待 R1 修復後執行

*處理完畢後，請把最終結論（含回測數字）補進 `docs/judgment-cases.md` 與 `screen_short.py` 檔頭的「回測結論（待補充）」。*
