# GEMINI.md

本專案的代理行為準則與技術說明採單一來源，請務必遵守以下兩份檔案：

@AGENTS.md

@CLAUDE.md

（若你的版本不支援 `@` import 語法，請在開始任務前先完整閱讀根目錄的 `AGENTS.md` 與 `CLAUDE.md`。AGENTS.md 定義 Definition of Done、何時停下來問使用者、驗證紀律；CLAUDE.md 定義專案架構、常用命令與開發注意事項。）

## Gemini 專屬提醒

- 對話回覆一律使用繁體中文；git commit 使用英文
- 你沒有 Claude Code 的 subagent 機制，AGENTS.md 中的驗證紀律請由你自己在同一對話內執行：改動後實跑驗證命令、read-back 檔案、附證據再宣告完成
- 遇到 AGENTS.md「該停下來問使用者」的情境時，直接停下輸出問題，不要自行假設後繼續
