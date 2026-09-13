# Changelog

台股篩選器的開發紀錄。目的是讓不熟悉這個專案的人也能一眼看懂目前做了什麼、怎麼運作的。

## 專案架構速覽

- **前端**：`src/frontend/index.html`（純 HTML + Tailwind CSS CDN + Alpine.js，無建置流程）
- **後端**：`src/backend/`（FastAPI）
  - `main.py` — API 路由
  - `screener.py` — 篩選邏輯（讀 `stocks.json`，套條件，回傳清單）
  - `stock_list.py` — 從 TWSE/TPEX 抓全部台股代碼清單
  - `data_fetcher.py` — 每日排程用，批次抓 yfinance + FinMind，算技術指標，存成 `data/stocks.json`
  - `ai_insight.py` — 呼叫 Gemini API，產生單一股票的白話 AI 解讀（含快取）
- **資料**：全部存在 `data/*.json`，沒有資料庫。`stocks.json` 是唯一資料來源，前端每次篩選都是即時讀這個檔案
- **排程**：`run_fetch.bat` 給 Windows 工作排程器每天早上跑一次，重新產生 `stocks.json`

---

## 2026-08-02 — 新增 AI 股票解讀功能

**動機**：使用者點進單一股票詳情時，除了看到原始技術面/基本面數字，希望有白話解讀幫助不懂股票的人理解這些數字代表什麼。

**怎麼做的：**
- 新增 `GET /api/stocks/{code}/insight` API，讀取該股票在 `stocks.json` 裡既有的數值（價格、均線、RSI、MACD、PE、EPS、營收年增率），組成 prompt 丟給 Gemini（`gemini-flash-latest`），用 `response_schema` 強制輸出結構化 JSON（一句話總結、技術面解讀、基本面解讀、留意重點）
- **刻意限制**：AI 只解讀既有數據，不查即時新聞、不做未來預測、不給買賣建議，並在每次回應附上免責聲明
- **快取機制**：結果依「股號 + 資料更新日期」存在 `data/ai_cache.json`，同一天同一支股票不會重複呼叫 Gemini（避免燒 API 額度、加快回應）；使用者要主動點「取得 AI 解讀」才會呼叫，不會自動幫全部股票先跑一輪
- 前端加了「詳情」按鈕與 modal，顯示原始數據 + AI 解讀區塊
- Gemini API key 存在 `.env` 的 `GEMINI_API_KEY`，只在後端使用，前端拿不到

**踩過的坑：**
- 一開始用 `gemini-2.5-flash`，新申請的 API key 打不通（Google 對新帳號停用了這個模型），改成 `gemini-flash-latest`（Google 官方的「永遠指向目前建議版本」別名），可避免以後 Google 換版又壞掉

## 2026-08-02 — 修復篩選 API 500 錯誤（資料異常值）

**問題**：按「開始篩選」偶爾會出現 `Unexpected token 'I', "Internal S"... is not valid JSON` 錯誤。

**原因**：`stocks.json` 裡有 2 支股票（3073、8092）的 PE 值是 `Infinity`——yfinance 對這兩家公司回傳的本益比本身就是無限大（通常代表 EPS 極接近 0）。JSON 規格不允許輸出 `Infinity`，只要篩選結果包含這兩支，後端序列化回應時就會直接丟未處理的例外，前端收到的不是 JSON 而是伺服器錯誤頁。

**怎麼修的：**
- `screener.py` — 篩選結果輸出前統一把 `inf`/`nan` 轉成 `null`（防禦性修復，之後任何來源的資料異常都不會再讓整支 API 掛掉）
- `data_fetcher.py` — 抓資料當下就過濾掉不合法的 PE/EPS 數值，明天排程重跑就不會再產生這種資料
- 直接修補現有的 `data/stocks.json`，不用等明天重新抓一次（原本抓一次要 30–60 分鐘）
