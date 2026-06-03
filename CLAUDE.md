# NCCU 課程推薦系統 — 專案說明（CLAUDE.md）

> 給 AI agent 的專案全貌速覽。讀完即可掌握架構、檔案、計畫與慣例。

## 一句話

政大全校課程推薦 PoC：使用者選職涯（或自由提問）→ Gemini File Search 從 114-2 全校 2,877 門課綱做 RAG → 回傳分組課程推薦 / 多輪問答，附課綱連結。

## 兩大功能模式

1. **職涯推薦**（`POST /recommend`）：50 種職涯之一 → 兩階段 Gemini（retrieval → 分組）→ core/supporting/extended 三組課程卡，每張含結構化推薦理由（lead + 粗體 bullet）+ 課綱連結。
2. **自由問答**（`POST /qa`）：任意課程問題 → Gemini Interactions API 多輪 RAG → 答案 + citations（課綱來源）+ followup 建議。session/turn 持久化於 Postgres。

## 技術棧

| 層 | 技術 |
|----|------|
| RAG | Gemini File Search Store（`gemini-2.5-flash`），google-genai 1.68.0 |
| Backend | Python 3.11 + FastAPI，asyncpg |
| Frontend | 原生 HTML/CSS/JS（無框架），navy glassmorphism「指揮台」風格 |
| DB | PostgreSQL（query_log + qa_session + qa_turn） |
| 部署 | Railway（backend Docker + frontend static） |

## 目錄結構

```
NCCU-poc/
├── ingestion/              # 一次性：建知識庫（本地執行，非部署）
│   ├── run.py              # 入口：XLSX→爬課綱→skill tag→上傳 File Search Store
│   ├── xlsx_parser.py      # 下載 CoursesList.xlsx，解析 2877 課，course_id→URL 公式
│   ├── scraper.py          # async httpx 抓課綱 HTML，BeautifulSoup 萃取文字
│   ├── skill_tagger.py     # 批次 Gemini 生成「技能橋接」段落（職涯↔課程語義橋）
│   └── uploader.py         # 上傳文件至 Gemini File Search Store（含 custom_metadata）
│
├── backend/
│   ├── main.py             # FastAPI app：/health /recommend /qa /qa/session/{id}
│   ├── models.py           # Pydantic：RecommendRequest/Response, QaRequest/Response, Reason, Citation
│   ├── recommend.py        # 職涯推薦兩階段 pipeline + metadata join + dedup
│   ├── judge.py            # 推薦模式 LLM judge（4維：relevance/grouping/reason/diversity）
│   ├── qa.py               # 問答 pipeline：interactions API、citation 萃取、答案解析
│   ├── qa_judge.py         # 問答 LLM judge（RAGAS 3維：faithfulness/relevancy/context_precision）
│   ├── qa_logger.py        # qa_session/qa_turn 持久化與讀回
│   ├── logger.py           # query_log 寫入（推薦模式）+ judge 分數更新
│   ├── db.py               # asyncpg pool（DATABASE_URL 未設則 logging 優雅跳過）
│   ├── career_skills.json  # 50 職涯 → 技能關鍵字（靜態，人工定義）
│   ├── courses_meta.json   # 2877 課 metadata（ingestion 生成，course_id→name/dept/teacher/url）
│   ├── schema.sql          # query_log + qa_session + qa_turn 建表
│   ├── Dockerfile          # package-style：uvicorn backend.main:app，context=repo root
│   └── requirements.txt
│
├── frontend/
│   ├── index.html          # 雙模式 SPA：mode chips + 推薦容器 + 問答容器 + sticky composer
│   ├── style.css           # navy glassmorphism；推薦樣式 + Q&A 樣式（append 區段）
│   ├── app.js              # 模式切換、autocomplete、offcanvas、/recommend、/qa、對話渲染
│   ├── careers.js          # 50 職涯清單 + 6 熱門（由 career_skills.json 生成，需同步）
│   └── mockup-qa.html      # Q&A 設計稿（獨立預覽，非正式檔）
│
├── tests/                  # pytest，49 passing
│   ├── ingestion/          # xlsx_parser, scraper, skill_tagger
│   └── backend/            # recommend, api, judge, logger, qa, qa_judge
│
├── docs/
│   ├── analytics.sql       # 營運分析查詢（熱門職涯、錯誤率、latency、judge 分數、session 輪數）
│   └── superpowers/
│       ├── specs/          # 設計文件（brainstorming 產出）
│       │   ├── 2026-06-03-nccu-course-recommender-design.md
│       │   └── 2026-06-03-rag-qa-mode-design.md
│       └── plans/          # 實作計畫（writing-plans 產出）
│           ├── 2026-06-03-plan-1-ingestion.md
│           ├── 2026-06-03-plan-2-backend.md
│           ├── 2026-06-03-plan-3-frontend.md
│           └── 2026-06-03-plan-4-logging.md
│
├── railway.toml            # Railway 部署設定
├── .env.example
└── HANDOFF.md              # 專案進展史 + WBS + 待辦（讀這份看「做到哪」）
```

## 關鍵設計決策（踩過的坑）

1. **資料來源**：政大官方 `CoursesList.xlsx`（2877 課，無需爬蟲）。`es.nccu.edu.tw` API 有 500 筆上限，故用 XLSX。課綱頁 `newdoc.nccu.edu.tw/teaschm/1142/schmPrv.jsp-...` 為 server-rendered HTML，可直接抓。
2. **course_id → 課綱 URL 公式**：9 碼 course_id `000211012` → `num=000211&gop=01&s=2`。
3. **SSL legacy**：NCCU 伺服器用舊版 TLS，需 `ssl.OP_LEGACY_SERVER_CONNECT` / httpx `verify=False`。**此為已知必要設計，非安全漏洞。**
4. **Gemini File Search 不能配 `response_mime_type=json`**：用 file_search tool 時答案是自由文字，需從文字解析 JSON（推薦 stage1 用 `extract_json_array`；問答用 `parse_qa_response`）。
5. **Interactions API outputs 是扁平 list**：答案在 `type=="text"` item 的 `.text`；citations 在該 item 的 `annotations[].source`（含「課程代號: 9碼」）。多輪用 `previous_interaction_id`（server 端存歷史）。
6. **SDK 細節**（google-genai 1.68.0）：`file_search_stores.create` 需 `config={"display_name":...}`；`import_file` op 無 `.result()`，需 poll `op.done`。
7. **兩階段非阻塞 logging**：Phase 1 立即寫 metadata，Phase 2 背景跑 LLM judge → UPDATE。`DATABASE_URL` 未設時 logging 優雅跳過（但 `/qa` 強依賴 DB → 無 DB 回 503）。
8. **結構化推薦理由**：`reason = {lead, points:[{term,detail}]}`，前端渲染 lead + 粗體 term bullet。

## 環境變數

- `GEMINI_API_KEY`：[aistudio.google.com/apikey](https://aistudio.google.com/apikey)
- `FILE_SEARCH_STORE_NAME`：ingestion/run.py 執行後輸出
- `DATABASE_URL`：Railway Postgres（backend logging/QA 用）
- `ALLOWED_ORIGIN`：frontend URL（CORS）
- 前端 `app.js` 的 `CONFIG.API_URL`：localhost 自動偵測，部署前手動填 backend URL

## 開發慣例

- **流程**：brainstorming（spec）→ writing-plans（plan）→ subagent-driven-development（TDD 實作）
- **TDD**：寫測試 → 看失敗 → 實作 → 看通過 → commit。每個檔案一個 commit。
- **commit**：conventional commits（feat/fix/chore/docs/refactor）
- **Git remote**：`git@github.com:albertpeng678/NCCU-POC.git`（用 github-personal SSH key）
- **平台**：Windows。temp 用 `tempfile.gettempdir()`，非 `/tmp`。
