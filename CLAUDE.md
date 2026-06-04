# NCCU 課程推薦系統 — 專案說明（CLAUDE.md）

> 給 AI agent 的專案全貌速覽。讀完即可掌握架構、檔案、計畫與慣例。
> 最後更新：2026-06-04（Session 2）。**接手前務必讀 `HANDOFF.md`「零、最優先事項」**。

## 一句話

政大全校課程推薦 PoC：使用者選職涯（或自由提問）→ Gemini File Search 從 114-2 全校課綱（實際 **2,718 門**，非早期估的 2877）做 RAG → 回傳分組課程推薦 / 多輪問答，附課綱連結。

## 兩大功能模式

1. **職涯推薦**（`POST /recommend`）：
   - **50 種預設職涯**之一 → 用 `career_skills.json` 靜態技能。
   - **清單外職涯**（如「記者」「清潔工」）→ `derive_skills_for_career` 用 LLM 即時推導「可轉移能力」技能 → 真實檢索；推不出（亂打）或檢索空 → 回 `no_match`（前端溫和導向問答，不卡死）。
   - 兩階段 Gemini（stage1 file_search 檢索 → stage2 分組）→ core/supporting/extended 三組課程卡，每張含結構化推薦理由（lead + 粗體 bullet）+ 課綱連結。
   - **多樣性「換一批」**：stage1 取候選池(POOL_SIZE) → `sample_candidates`（定錨 ANCHOR_COUNT + seed 隨機抽 SAMPLE_SIZE）→ 同職涯每次回不同但相關的組合。`/recommend` 可帶 `seed`（未帶則後端亂數）；response 回 `seed`。
   - **去重**：`deduplicate_by_prefix`（course_id 前6碼）+ `deduplicate_by_name`（課名，避免跨掛同名課重複）。
2. **自由問答**（`POST /qa`）：任意課程問題 → Gemini Interactions API 多輪 RAG → 答案（**markdown 表格 + 克制粗體 + 文字說明**）+ citations（課綱來源）+ followup 建議。session/turn 持久化於 Postgres。離題溫和引導、citations 空時覆寫「查無資料」防幻覺。

## 技術棧

| 層 | 技術 |
|----|------|
| RAG | Gemini File Search Store（生成模型 **`gemini-2.5-flash`** — 見設計決策 #12，**不可換 flash-lite**），**google-genai 2.7.0** |
| Backend | Python 3.11 + FastAPI，asyncpg |
| Frontend | 原生 HTML/CSS/JS（無框架），navy glassmorphism「指揮台」風格；前端引入 marked + DOMPurify（Q&A markdown 渲染）+ @sentry/browser CDN |
| DB | PostgreSQL（query_log + qa_session + qa_turn）— Railway Postgres |
| 觀測 | **Sentry**（後端 sentry-sdk[fastapi] + 前端 @sentry/browser，DSN env/config 驅動；專案 `nccu-poc` @ org `albert-ar`） |
| 部署 | Railway（backend Docker + frontend nginx static），**走 git push**（GitHub-connected service，非 `railway up`） |

## 目錄結構

```
NCCU-poc/
├── ingestion/              # 一次性：建知識庫（本地執行，非部署）
│   ├── run.py              # 入口：XLSX→爬課綱→skill tag→上傳 File Search Store（ThreadPool 並行+timeout+docs_cache）
│   ├── xlsx_parser.py      # 下載 CoursesList.xlsx，解析課程（跳過非9碼course_id + 依course_id去重 → 2718 唯一課）
│   ├── scraper.py          # async httpx 抓課綱 HTML，BeautifulSoup 萃取文字（SSL legacy）
│   ├── skill_tagger.py     # 批次 Gemini 生成「技能橋接」段落（ThreadPoolExecutor x8 並行）
│   └── uploader.py         # files.upload + import_file（poll 240s）
│
├── backend/
│   ├── main.py             # FastAPI：/health /recommend /qa /qa/session/{id}；Sentry init；client 含 429/503 退避重試
│   ├── models.py           # Pydantic：RecommendRequest(seed)/Response(seed,notice)、NoMatchResponse、QaRequest/Response
│   ├── recommend.py        # 兩階段 pipeline + sample_candidates(多樣性) + derive_skills_for_career(清單外) + dedup
│   ├── judge.py            # 推薦模式 LLM judge
│   ├── qa.py               # 問答 pipeline：interactions API、citation/text 萃取(雙SDK相容)、格式驗證+重試、防幻覺
│   ├── qa_judge.py         # 問答 LLM judge（RAGAS 3維）
│   ├── qa_logger.py        # qa_session/qa_turn 持久化與讀回
│   ├── logger.py           # query_log 寫入 + judge 分數更新
│   ├── db.py               # asyncpg pool（DATABASE_URL 未設則優雅跳過）
│   ├── career_skills.json  # 50 職涯 → 技能關鍵字（靜態）
│   ├── courses_meta.json   # 2718 課 metadata（已 commit；backend runtime 依賴）
│   ├── schema.sql          # query_log + qa_session + qa_turn 建表
│   ├── Dockerfile          # uvicorn backend.main:app，context=repo root
│   └── requirements.txt    # 含 sentry-sdk[fastapi]
│
├── frontend/
│   ├── index.html          # 雙模式 SPA；引入 Sentry/marked/DOMPurify CDN；資源連結帶 ?v=N（cache-bust）
│   ├── style.css           # navy glassmorphism；推薦/Q&A/markdown/等待UX/換一批/RWD 樣式
│   ├── app.js              # 模式切換、autocomplete、/recommend、/qa、markdown渲染、換一批、等待UX、CONFIG(API_URL,SENTRY_DSN)
│   ├── careers.js          # 50 職涯清單 + 6 熱門
│   ├── Dockerfile          # nginx:1.27-alpine + envsubst $PORT
│   └── nginx.conf.template # listen $PORT + SPA fallback
│
├── scripts/                # 本機驗證/復原工具（gitignored，但 backfill_*.py 已 force-add 保留）
│   ├── backfill_async.py   # ⚡ async + upload_to_file_search_store 高併發灌 store（~100課/分，0失敗）
│   ├── backfill_store.py   # 舊版 ThreadPool 兩步（慢，備用）
│   └── probe_*.py          # 一次性驗證探針
│
├── tests/                  # pytest，78 passing；conftest.py 測試時停用 Sentry
│
├── docs/superpowers/
│   ├── specs/              # 設計文件：…recommender / …rag-qa / 2026-06-04-recommendation-diversity / 2026-06-04-open-career-handling
│   └── plans/              # 實作計畫：plan-1~4 / 2026-06-04-recommendation-diversity / 2026-06-04-open-career-handling
│
├── ingestion/docs_cache.jsonl  # 2718 課文件文字快取（gitignored；backfill 用，免重爬/重標）
├── railway.toml            # Railway 部署設定（backend）
└── HANDOFF.md              # 專案進展史 + WBS + 待辦（接手必讀）
```

## 關鍵設計決策（踩過的坑）

1. **資料來源**：政大官方 `CoursesList.xlsx`。`es.nccu.edu.tw` API 有 500 筆上限，故用 XLSX。課綱頁 `newdoc.nccu.edu.tw/teaschm/1142/schmPrv.jsp-...` 為 server-rendered HTML，可直接抓。**xlsx_parser 跳過非9碼 course_id + 依 course_id 去重 → 2718 唯一課**（早期文件寫 2877 是含重複/估值）。
2. **course_id → 課綱 URL 公式**：9 碼 `000211012` → `num=000211&gop=01&s=2`。
3. **SSL legacy**：NCCU 伺服器用舊版 TLS，需 `ssl.OP_LEGACY_SERVER_CONNECT` / httpx `verify=False`。**已知必要設計，非漏洞。**
4. **Gemini File Search 不能配 `response_mime_type=json`**：file_search tool 時答案是自由文字，需從文字解析 JSON（推薦 stage1 用 `extract_json_array`；問答用 `parse_qa_response`）。
5. **google-genai 2.7.0 的 Interactions response 結構**（與 1.68.0 不同）：答案在 `r.output_text` 或 `r.steps[].content[]`（type=model_output→text）；citations 在 `content[].annotations[]`（file_citation）的 `custom_metadata.course_id`。`qa.py` 的 `extract_answer_text`/`extract_course_ids_from_grounding` **同時相容 1.68(.outputs) 與 2.7(.steps)**。
6. **SDK 細節**：`file_search_stores.create` 需 `config={"display_name":...}`；`import_file` 無 `.result()`，poll `op.done`；**一步上傳用 `upload_to_file_search_store`（含 async `client.aio...`）**。
7. **兩階段非阻塞 logging**：Phase 1 立即寫 metadata，Phase 2 背景跑 LLM judge → UPDATE。`DATABASE_URL` 未設時 logging 優雅跳過（但 `/qa` 強依賴 DB → 無 DB 回 503）。
8. **結構化推薦理由**：`reason = {lead, points:[{term,detail}]}`，前端渲染 lead + 粗體 term bullet。
9. **Ingestion 上傳的兩個坑**：(a) `import_file` 不耐高併發 → ThreadPool x8 同步兩步會讓 store 索引佇列**雪崩 timeout**；用 **async `upload_to_file_search_store` + semaphore(16)** 反而 ~100課/分 0 失敗（見 `scripts/backfill_async.py`）。(b) 大量 embedding 會吃 **Gemini 月度 spend cap** 與 **embedding 速率上限(429)**；查詢時**只嵌入問句、不重索引**（索引一次性）。
10. **推薦延遲**：~40-50s（兩階段 flash + RAG 本質）。**主因是 LLM 輸出 token 數**（web+context7 研究）。已做：降輸出量（POOL 40→24、理由精簡）、thinking 自動（**關掉會稀疏、品質崩，不可關**）、429/503 退避重試、前端**分步驟等待 UX**（NNgroup 感知優化）。**未解的提速方案見 HANDOFF「零」。**
11. **前端快取**：靜態資源連結帶 `?v=N`（cache-bust）；更新前端時 bump 版本，避免瀏覽器拿到舊 CSS/JS（曾導致「很醜/寬度跳/null.classList 崩」）。
12. **生成模型固定 `gemini-2.5-flash`**：使用者明確要求**不可為提速換 `gemini-2.5-flash-lite`**（lite 快 3.8x 但品質低約 10%）。提速只能用不犧牲品質的手段（降輸出/合併呼叫/快取/等待UX）。
13. **CORS 本機坑**：`.env` 的 `ALLOWED_ORIGIN` 是部署用佔位符（`https://your-frontend...`）；本機起 backend 要用 `ALLOWED_ORIGIN=* python -m uvicorn ...` 覆蓋，否則擋 localhost:3000。

## 環境變數（`.env`，gitignored）

- `GEMINI_API_KEY`：[aistudio.google.com/apikey](https://aistudio.google.com/apikey)。**換 key 就看不到既有 store。**
- `FILE_SEARCH_STORE_NAME`：目前 full store = **`fileSearchStores/nccucourses1142-znuka50qq2y2`**（2713 課；另 11 課待補）。
- `SENTRY_DSN`：Sentry 專案 nccu-poc 的 DSN（公開值）。未設則 Sentry 停用。
- `ALLOWED_ORIGIN`：部署填 frontend URL；**本機測試用環境變數 `ALLOWED_ORIGIN=*` 覆蓋**。
- `DATABASE_URL`：**本機**用 Railway Postgres 的 `DATABASE_PUBLIC_URL`（TCP proxy）以環境變數傳入（勿寫進 .env）；**部署**用 Railway 變數引用 `${{Postgres.DATABASE_URL}}`（私網）。
- 前端 `app.js` 的 `CONFIG.API_URL`（localhost 自動偵測，部署前填 backend URL）、`CONFIG.SENTRY_DSN`（已填，公開值）。

## 開發慣例

- **流程**：brainstorming（spec）→ writing-plans（plan）→ subagent-driven-development（TDD 實作）。
- **TDD**：寫測試 → 看失敗(RED) → 實作 → 看通過(GREEN) → commit。
- **驗證紀律**：修復/優化要過真實 e2e（Playwright 真後端），不只單元；前端視覺改動先截 PNG 給使用者確認再 commit。
- **commit**：conventional commits（feat/fix/chore/docs/refactor/perf）。每 commit 結尾 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。
- **Git remote**：`git@github.com:albertpeng678/NCCU-POC.git`（github-personal SSH key）。Session 2 分支 **`feat/poc-enhancements`**。
- **平台**：本機已切到 **macOS**（Session 1 是 Windows）。temp 用 `tempfile.gettempdir()`。
- **Gemini 用量**：勿短時間連續打大量請求（會撞 429 速率牆，污染延遲量測）。
