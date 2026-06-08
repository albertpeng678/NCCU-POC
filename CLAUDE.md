# NCCU 課程推薦系統 — 專案說明（CLAUDE.md）

> 給 AI agent 的專案全貌速覽。讀完即可掌握架構、檔案、計畫與慣例。
> 最後更新：2026-06-08（**Session 8**）。Session 7 摘要：離線預算 `career_budget`（50 職涯預算進 Postgres，命中秒回 0 即時 AI）＋柴犬等候動畫（Lottie 三色、done-driven 數字）＋Q&A 失敗復原（過載重試泡泡/查無資料引導）＋Sentry 降噪（暫時性 Gemini 錯誤降 warning、本機不啟用）＋多輪追問修復（history-based 統一）。**✅ Session 8 已修先前高優先 bug「Event loop is closed」（commit `d6b0490`）**：POST /recommend 改全程 async `await build_recommendation_instrumented_async`（不再 to_thread + asyncio.run），清單外 derive 也改 `await derive_skills_for_career_async`（見設計決策 #22）。已過嚴格 code review + Playwright 全端 e2e（polluter POST → victim stream 無 loop 錯誤）+ 256 後端測試綠，**已 push origin/master 部署**。前端 `?v=34`。注意 `~/.claude` 的 memory 不會跨機器，必要資訊都在 HANDOFF/CLAUDE。
>
> ── 歷史 ── Session 5 摘要：探索過 3.5-flash（原生 response_schema 免解析，但**現階段 high-demand 503 延遲不穩**，故未採為預設）→ 決定 **Q&A 留 2.5-flash 串流**，用 (A) JSON 三層強化（`parse_qa_response` 補 `json_repair`+`_strip_fences` 絕不漏鷹架；`stream_answer` JSON-first 守門用 `_partial_answer` 增量抽乾淨值）(B) 平滑串流打字機（SSE token 餵 `createTypewriter` 定速 **20cps** 緩衝 + `drainCount` backlog 自適應；**表格逐列滑順長出**——`safeMarkdownPrefix` 改「保留已完成列、只藏正在打的半截列」，非整塊 snap-in；`renderFinal`/committed 用 `mdToHtml` 不套 heal，避免結尾表格列被誤砍）壓掉「漏 JSON / 不平滑 / 表格 raw」。`QA_MODE=stream`(預設 2.5 強化) / `replay`(3.5 非串流選項)。Playwright 動態實測通過（無 JSON 鷹架、表格逐列建構、0 console error）。spec/plan：`docs/superpowers/{specs,plans}/2026-06-05-qa-2.5-hardening-smooth-stream*`。**已 push master 部署（Railway）；前端 cache-bust `?v=19`**。注意 `~/.claude` 的 memory 不會跨機器，所有必要資訊都已寫進 HANDOFF/CLAUDE。

## 一句話

政大全校課程推薦 PoC：使用者選職涯（或自由提問）→ Gemini File Search 從 114-2 全校課綱（實際 **2,718 門**，非早期估的 2877）做 RAG → 回傳分組課程推薦 / 多輪問答，附課綱連結。

## 兩大功能模式

1. **職涯推薦**（`POST /recommend`）：
   - **50 種預設職涯**之一 → 用 `career_skills.json` 靜態技能。
   - **清單外職涯**（如「記者」「清潔工」）→ `derive_skills_for_career` 用 LLM 即時推導「可轉移能力」技能 → 真實檢索；推不出（亂打）或檢索空 → 回 `no_match`（前端溫和導向問答，不卡死）。
   - **（Session 4 改 fan-out）** stage1＝**每技能一支並行小檢索**(`fanout_retrieve_async`，top_k=8，asyncio.gather 容錯) 合併成候選池 → stage2＝**整池標註**(`stage2_annotate_pool_async` 扁平 ranked + group) → `build_ranked_courses`。`RecommendResponse` 改**扁平 `courses: list[Course]`**(含 group/rank)+`batch_size`。**延遲 ~124s→~79s**（fan-out 44 + stage2 35）。⚠️ 代價：每請求 embedding 1→6-8 次（放大花費 + 撞 `gemini-embedding-001` 限流；見設計決策 #19）。
   - **「換一批」改前端分頁**：首載回整池，前端 `pagination.js` 依 rank 切批每批 10、`groupBatch` 排名分桶顯示三區；換一批**純前端切片 0 網路請求**；池乾以新 seed 續池。`/recommend` 可帶 `seed`；response 回 `seed`。
   - **去重**：`deduplicate_by_prefix`（course_id 前6碼）+ `deduplicate_by_name`（課名，避免跨掛同名課重複）。
2. **自由問答**（`POST /qa`）：任意課程問題 → Gemini Interactions API 多輪 RAG → 答案（**markdown 表格 + 克制粗體 + 文字說明**）+ citations（課綱來源）+ followup 建議。session/turn 持久化於 Postgres。離題溫和引導、citations 空時覆寫「查無資料」防幻覺。

## 技術棧

| 層 | 技術 |
|----|------|
| RAG | Gemini File Search Store（生成模型：**Q&A=`gemini-3.5-flash` 結構化串流**、**推薦=`gemini-2.5-flash`** — 見設計決策 #12，**不可換 flash-lite**），**google-genai 2.7.0** |
| Backend | Python 3.11 + FastAPI，asyncpg |
| Frontend | 原生 HTML/CSS/JS（無框架），navy glassmorphism「指揮台」風格；前端引入 marked + DOMPurify（Q&A markdown 渲染）+ @sentry/browser CDN |
| DB | PostgreSQL（query_log + qa_session + qa_turn）— Railway Postgres |
| 觀測 | **Sentry**（後端 sentry-sdk[fastapi] + 前端 @sentry/browser，DSN env/config 驅動；專案 `nccu-poc` @ org `albert-ar`） |
| 部署 | Railway，**走 git push**（GitHub-connected，非 `railway up`）。**（Session 4 改同源）單一 service：backend FastAPI `StaticFiles` 直接服務前端**（因 root `railway.toml` 跨服務污染、第二個 nginx service 會誤 build 後端）→ **單一網址 https://nccu-course.up.railway.app**（使用者於 Railway 改過；舊 `nccu-poc-production…` 已失效回 "Application not found"），前後端同源、零 CORS。Dockerfile shell-form `${PORT}`、`init_pool` 連不到 DB 不崩。**前端靜態檔用整包 `COPY frontend/ ./frontend/`（勿改回寫死列舉——Session 8 曾因列舉漏檔導致 app.js ESM import 404 → 全站死；`tests/backend/test_frontend_assets_shipped.py` 守門）。** |

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
10. **推薦延遲**：**主因是 LLM 輸出 token 數**（web+context7 研究）。已做：降輸出量（POOL 40→24、理由精簡）、429/503 退避重試、前端**分步驟等待 UX**。**（Session 6 更新）compose（`stage2_annotate_pool_async` 整池標註）已設 `thinking_budget=0`**：A/B 實測（`scripts/probe_compose_thinking_ab.py`）此「分類+排序+短理由」任務關 thinking **快 ~3x（33s→11s）且 judge 品質不掉**（reason 維持滿分）。⚠️ 舊註「thinking 不可關（品質崩）」是**舊版自由文字 compose** 的結論，**不適用現在的 `response_schema` 版**（schema 約束輸出、思考邊際效益低）。**仍勿關 Q&A 串流的 thinking**（那是 grounding/品質相關，另案）。
11. **前端快取**：靜態資源連結帶 `?v=N`（cache-bust）；更新前端時 bump 版本，避免瀏覽器拿到舊 CSS/JS（曾導致「很醜/寬度跳/null.classList 崩」）。
12. **生成模型**：**（Session 8 更新）Q&A 預設 `gemini-3.5-flash` 原生結構化串流**（`response_schema`+`file_search`+`thinking_level=low`；治本幻覺/citation bug，見 spec `2026-06-08-qa-gemini-3.5`），可 env `QA_MODE` 切換：`stream35`(預設 3.5 結構化串流) / `stream`(2.5 串流 fallback) / `replay`(3.5 非串流)。**推薦仍 `gemini-2.5-flash`**（下一輪遷移）。⚠️ **仍不可為提速換 `flash-lite`**（lite 快但品質低約 10%）；提速只能用不犧牲品質的手段（降 thinking_level/降輸出/合併呼叫/快取/等待UX）。
13. **CORS 本機坑**：`.env` 的 `ALLOWED_ORIGIN` 是部署用佔位符（`https://your-frontend...`）；本機起 backend 要用 `ALLOWED_ORIGIN=* python -m uvicorn ...` 覆蓋，否則擋 localhost:3000。
14. **SSE 串流（Session 3）**：新增 `GET /recommend/stream`（5 階段事件）、`GET /qa/stream`（逐 token + done）；舊 `POST` 保留當 fallback。前端 EventSource + 階段 stepper + 打字機。**詳見 HANDOFF「★ Session 3」**。
15. **grounding 對 prompt 極敏感（不可踩）**：`config.system_instruction` 的人設會讓 2.5-flash 跳過 file_search → 罐頭答案。qa.py 的 `_SYSTEM_INSTRUCTION` 首段「【鐵則・最高優先】先檢索」**不可移除**。**拿掉 JSON 包裝改純 Markdown 會破壞 grounding（已回退）。** **（Session 8）「勿關 Q&A 串流 thinking」此結論僅適用 2.5**——3.5 GA 上 `file_search + response_schema + thinking_level=low` 實測 grounding 完整、citation 正確（見 spec `2026-06-08-qa-gemini-3.5`）；唯仍**勿用 `include_thoughts`**。另：設計決策 #4「File Search 不能配 `response_mime_type=json`」**僅適用 2.5**；**3.5 可 file_search + response_schema 並用**（這正是 Q&A 遷移 3.5 的主因）。
16. **429 真因＝SDK 預設 timeout 60s**（非單純速率）：recommend ~80-100s > 60s → 逾時→重試→請求分裂→燒 RPM。解：`HttpOptions(timeout=180_000)`（main.py）。embedding 429 同源（重試重複嵌入問句，free tier 100 RPM）。
17. **（Session 4）embedding 429「常態化」真因＝`gemini-embedding-001` server-side 區域限流**（Google 自 2025 末承認、無 ETA；連 65 檔小庫都有人中）。**逐層排除有量測**：非 spend cap（cap NT$500、用 23%、無「spending cap」字樣）、非 store 壞（`get` → 2714 active/0 failed/19.75MB；**受控實驗**新建 test store 與舊 store「同進同退」→ 帳號層級非 store）、非 RPD（Tier1 embedding RPD unlimited）。**被「backfill 重嵌 2718 課」+「fan-out 6-8 並行 query embed」放大**。緩解＝退避(已有)+停 burst+限併發/快取；**勿重建 store**。
18. **（Session 4）fan-out 取捨**：延遲 124s→79s，但每請求 embedding 1→6-8 次（放大花費 + 撞限流）。減量(更小 top_k/池)可省 ~10-20s + 降 embedding 壓力；真正「快」要離線預算 50 職涯快取(phase 2)。
19. **（Session 4）citation 用結構化 `custom_metadata`、思維鏈用 `part.thought` 過濾**：grounding citation 從「regex 掃文字找代號」改讀 `grounding_chunks[].retrieved_context.custom_metadata.course_id`（官方法，修文件中段 chunk 漏顯示）；串流答案用 `_visible_text_from_chunk`(`part.thought` 過濾) 杜絕 reasoning/`executable_code` 外洩。Q&A `FileSearch(top_k=5)` 固定參考課綱數（不設會浮動到 14）。
20. **（Session 4）Q&A 漸進 markdown 渲染**：`progressive-md.js` rAF 節流重渲染累積緩衝（復用 `renderSafeMarkdown` = marked+DOMPurify，安全不變）→ 表格/粗體邊串流邊成形；done 仍權威覆蓋。`stream_answer` token handler 改用之，fallback typewriter 保留。
21. **（Session 7）離線預算 `career_budget` 秒回**：50 固定職涯離線跑完整 pipeline → 整池存 Postgres `career_budget` 表；線上 `get_budget` 命中即**秒出（0 即時 AI）**，未命中落回即時路徑。`backend/career_budget.py`（serialize/deserialize/get_budget/upsert_budget；deserialize 補 group/reason 預設、跳壞課防形狀漂移）；`recommend.py` `stream_recommendation_from_budget`（命中時 SSE 5 階段瞬間）；離線腳本 `scripts/build_career_budget.py`（`ONLY`/`ONLY_MISSING`/`CONCURRENCY` env）。**正式 DB 已灌 50/50**（PM 1.24s 等實測）。⚠️ **命中預算時務必 `not budget` gate 掉背景 judge**（否則秒回卻仍背景燒 AI；code-review 抓過）。
22. **（Session 7 發現 → Session 8 已修 `d6b0490`）`asyncio.to_thread` + 共用 async client 跨 loop 污染**：POST /recommend 即時路徑曾用同步 `build_recommendation_instrumented`（內部 `asyncio.run(_run())`，`_run` 用模組層共用 `_client.aio`）。commit `21e6a93` 為修「asyncio.run in running loop」改 `await asyncio.to_thread(...)` → 但 worker thread 的 `asyncio.run` 開**用完即關的 loop**，把共用 `_client.aio` 的 httpx client **綁到死 loop** → 之後主 loop 上所有 `/recommend/stream`(清單外)、`/qa/stream` 噴 **「Event loop is closed」**（把單請求崩潰換成污染整個 worker 的更廣 regression；單元測試/code-review 沒抓到，e2e 才抓到）。**修法（`d6b0490`）：POST /recommend 改 async、直接 `await build_recommendation_instrumented_async`（全程主 loop，不開新 loop、不 to_thread）；清單外 derive 也改 `await derive_skills_for_career_async`**。同步薄包裝 `build_recommendation_instrumented`（仍 asyncio.run）只留給 sync 呼叫端（`build_recommendation` / sync 測試 / scripts）——**伺服器路徑勿用**。回歸測試 `tests/backend/test_recommend_loop_fix.py` 鎖定「pipeline 兩階段都在呼叫端 loop」；驗證門檻：真後端 e2e「先打一次 POST /recommend 清單外/即時 → 再打 /recommend/stream + /qa/stream」確認不再 closed（已過）。

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
