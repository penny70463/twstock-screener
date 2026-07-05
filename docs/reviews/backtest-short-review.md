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

**原作者回應**：（待填）

## 發現 2（嚴重）：缺 regime 閘門

生產版 `screen_short.py:56` 只在 `is_regime_allowed(regime, "short")`（mixed/bearish）時執行做空；
回測版沒有 import `market_regime`，等於多頭年份也天天進場。2021/2024 這類多頭年的虧損交易
在真實運作中不會發生，會系統性低估策略績效。

**建議**：逐評分日重演 regime（`get_regime(asof)` 已支援歷史日期，但內部只抓 2y TWII，需擴至涵蓋回測區間）。

**原作者回應**：（待填）

## 發現 3（中）：股票池為 30 檔硬編碼權值股

`backtest_short.py:276-283` 硬編碼台積電、鴻海、台塑等權值股（且 2412、2409、2303 重複）。
生產版讀 `universe_tw.json` 全池。「跌破季線＋營收大減＋有融券」的做空標的多為中小型股，
權值股樣本測不到策略的實際獵物。

**建議**：改讀 `universe_tw.json`；並在輸出 note 標註存活者偏誤（今日股票池回溯歷史，
不含已下市股 → 做空績效被低估，屬保守側）。

**原作者回應**：（待填）

## 發現 4（中）：條件 2、3 未實作但 docstring 宣稱四條件

`backtest_short.py:291` 註明「其他條件（融券、營收）假設恆為真」，但檔頭 docstring 列出四條件，
輸出報告若不標註會誤導。另 docstring 寫「月營收**年減** > 30%」，生產版 `screen_short.py:142`
用的是 `mom`（**月減**）——描述與生產版不一致。

**建議**：歷史融券與月營收可用 FinMind 補（`.env` 已有 FINMIND_TOKEN；
dataset：`TaiwanStockMarginPurchaseShortSale`、`TaiwanStockMonthRevenue`），
注意營收公佈時滯（評分日只能用已公佈的上期資料，防 lookahead）。
暫不補的話，至少在輸出 JSON 的 note 與年度統計旁明示「僅條件 1+4」。

**原作者回應**：（待填）

## 發現 5（低，效能）：逐股逐日重複下載

`_filter_short_candidates` 對每個評分日、每檔股票各發一次 `yf.download`（200 天窗）。
全區間逐週評分會產生上萬次請求，會被限流且極慢。
**建議**：一次性批次下載整段區間（參考 `screen_short.py:71` 的 `group_by="ticker"` 作法），
記憶體內截斷重演，並落地 cache pkl（本 repo 慣例：`src/advisor/cache/` 日期後綴）。

**原作者回應**：（待填）

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

**原作者回應**：（待填）

### 新發現 R2（中，正確性）：融券查詢回傳錯誤股票的值

`backtest_short.py:322-324`：填完快取後，用**迴圈最後一列**殘留的 `cache_key` 判斷並回傳——
每個日期第一次查詢的股票會拿到「該日全市場最後一列」的融券餘額；
要查的股票不在該市場清單時（如上櫃股），也會誤回最後一列的值而非 0。
應改為 `key = (check_date, code)` 後查 `_margin_cache[key]`。

**原作者回應**：（待填）

### 新發現 R3（低，效能）：進出場計算重複下載

`_calculate_entry_exit`（`backtest_short.py:427`）對每筆成交再 per-stock 下載 200 天——
`history_data` 已含同區間資料，直接傳入查表即可。主下載迴圈（`backtest_short.py:95`）
仍為逐股序列下載，建議改 `yf.download(list, group_by="ticker")` 批次（生產版 `screen_short.py:71` 慣例）。

**原作者回應**：（待填）

### 流程提醒

第一輪五條的「原作者回應」仍為（待填）。commit 訊息不構成審查回覆——請在本檔內逐條填寫
（尤其發現 1 需確認：反向是筆誤而非刻意變體？從修復行為推斷是筆誤，請明示以便結案），
連同 R1–R3 的回應一併 commit push。

---

## 裁決紀錄（使用者填或由任一 session 代記）

- 條件 4 採破底型（生產版）／反彈型（變體）／兩者都測：＿＿＿
- 其餘發現的處理結論：＿＿＿

*處理完畢後，請把最終結論（含回測數字）補進 `docs/judgment-cases.md` 與 `screen_short.py` 檔頭的「回測結論（待補充）」。*
