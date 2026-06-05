# NCCU 課程推薦系統 — 交接文件（HANDOFF.md）

> 專案進展史 + WBS + 待辦。給接手的 agent 快速掌握「做到哪、還剩什麼」。
> 最後更新：2026-06-05（**Session 5**：Q&A 留 2.5 + JSON 三層強化 + 平滑串流打字機；3.5 探索後 parked）

---

## ★ Session 5 交接（最新，換機器接手必讀）★

**做了什麼**：解決 Q&A 三個體感問題 ——「串流會漏 JSON 鷹架 / Gemini 一坨一坨吐 chunk 不平滑 / 表格以 raw markdown 顯示」。

**關鍵決策**：探索過 `gemini-3.5-flash`（掛 file_search 時能原生 `response_schema`，2.5 不行會 400 → 從源頭免「自由文字解析 JSON」）。但 **3.5 現階段 high-demand 503 嚴重、延遲變異大（13~39s）**，production 跑 2.5 才穩 → **決定 Q&A 留 2.5 串流**，用程式手段壓問題。3.5 成果保留：`answer_question_structured`（replay 模式）已 commit；3.5 *真串流* 實驗在 `git stash`。

**怎麼解（已實作於 `feat/qa-hardening-smooth-stream`，未上線）**：
- **(A) JSON 三層強化**：`parse_qa_response` → `json.loads`→`json_repair`→`_strip_fences`（剝殼後仍以 `{` 開頭就 blank，**絕不漏鷹架**）；`stream_answer` 加 **JSON-first 守門**（模型若沒寫 prose 直接吐 JSON，用 `_partial_answer`(json_repair 增量)只串乾淨 answer 值）。
- **(B) 平滑串流打字機**：SSE `token` 改餵既有 `createTypewriter`（定速緩衝，解耦 Gemini 不均 chunk）；`drainCount` backlog 自適應（buffer 大就一次吐多字追上生成）；live 尾端 `renderSafeMarkdown`（healed markdown → 表格 snap-in、不露 raw `|`、粗體即時）。
- **(C) `QA_MODE`**：預設 `stream`(2.5 強化)；`replay`(3.5 非串流，慢但結構上不漏)為 env 一鍵回退選項。

**驗證**：後端 205 + 前端 20 測試綠；**Playwright MCP 真後端實測通過**（串流中+最終皆無 `{"answer"`/```json、表格渲染成 `<table>`、citations 3 + followups 3、0 console error）。

**429 釐清**：本機開發時撞的 embedding 429 是**爆量測試**造成（每提問都 embed 問句 + SDK 重試放大），**production 正常**；使用者是 Tier 1。非阻礙。

**待辦/未做**：① 上線（git push 觸發 Railway；本輪只到分支驗證）。② `/recommend` 是否遷 3.5（另議，先 probe 延遲/品質）。③ 清理：刪 `createProgressiveRenderer`/`stripInProgressTable` 死碼、收斂單一渲染器。④ Layer 4「sentinel 換格式」徹底根治（需重驗 grounding #15）。⑤ git committer email 是公司帳號，push 前宜設個人帳號。

**spec/plan**：`docs/superpowers/{specs,plans}/2026-06-05-qa-2.5-hardening-smooth-stream*`。

---

## ★ Session 4 交接 ★

> 分支 **`feat/streaming-ux`**（已 push 到 master，Railway 自動部署）。本回合大量功能 + 除錯，**全部 TDD 單元 + 多數 live e2e 親證**。後端測試 **196 passing**。

### 已完成且已部署
1. **推薦提速 fan-out（#11，核心）**：舊「單次大檢索」(~124s) → **每技能一支並行小檢索(top_k=8) + 整池標註 + 前端分頁換一批**。
   - `recommend.py`：`fanout_query_skill_async` / `merge_fanout_results`(round-robin+去重+池上限) / `fanout_retrieve_async`(gather容錯) / `stage2_annotate_pool_async`(扁平 ranked) / `build_ranked_courses`。
   - `models.py`：`RecommendResponse` 改**扁平 `courses: list[Course]`**（每課含 group/rank）+ `batch_size`。
   - 前端 `pagination.js`（node:test 9/9）：**換一批純前端切片 0 網路請求**；池乾以新 seed 續池。`groupBatch` 改**批內排名分桶**（前40%core/中35%supporting/餘extended）→ 修「後端全標 core→前端只一區」。
   - **live 實測：fan-out 44s + stage2 35s ≈ 79s**（vs 124s，快約 1/3）。換一批 0 網路親證、PNG 存證。
2. **Q&A 無 DB 優雅降級（#13-15）**：`qa_logger._EphemeralStore`，無 `DATABASE_URL` 時用 uuid4 + in-memory 多輪 session；移除 `/qa`、`/qa/stream` 的硬性 503 死路。**live 親證**：無 DB 單輪+多輪上下文正常、不跳「Cannot create session」。
3. **citation 漏顯示修法**：streaming 改讀**結構化 `custom_metadata.course_id`**（官方 canonical 法）+ regex fallback。探針鐵證：5 門 ground 舊版只顯示 4（文件中段 chunk 無代號標頭被 regex 漏）。live ×2 親證。
4. **思維鏈外洩修法**：`stream_answer` 改 `_visible_text_from_chunk`（`part.thought` 過濾，官方法）→ 不再把 reasoning/`executable_code`(工具呼叫) 串給使用者。live 親證（回應真含 executable_code，正確排除）。
5. **Q&A `top_k=5`**：file_search 明確設 top_k（不設會浮動，實測曾吐 14 筆）→ 參考課綱穩定 5。
6. **部署修復（同源）**：`$PORT` 未展開(Dockerfile shell form) + `init_pool` 韌性(連不到 DB 不崩) + **backend 同源服務前端**(FastAPI StaticFiles，因 root `railway.toml` 跨服務污染、第二個 nginx service 會誤 build 後端)。**單一網址 https://nccu-poc-production.up.railway.app**，git push 觸發。
7. 進度條 eta 校準至 fan-out 實測（STAGE_SEC retrieve 44/compose 32）。
8. **Q&A 漸進式 markdown 渲染**：新 `frontend/progressive-md.js`（`createProgressiveRenderer`，rAF 節流 + 累積緩衝重渲染，node:test 4/4）；`stream_answer` token handler 從純文字打字機 → **邊串流邊用 `renderSafeMarkdown`(marked+DOMPurify) 漸進渲染**（表格/粗體/標題隨完成即現）；done 仍權威覆蓋；fallback typewriter 保留。**code-reviewer 通過、Playwright live 親證**（done 前 `<table>` 3列+`<strong>` 已漸進、0 console 錯）。Dockerfile 補 COPY、前端 **`?v=18`**。

### 🔑 embedding 429「常態化」的最終根因（耗大量篇幅查清，務必看）
**症狀**：推薦只回 1 門 / Q&A citation 對不上 / file_search 持續間歇 429「Failed to embed content」。
**逐層排除（全有量測）**：
- **不是 spend cap**：cap 已調 NT$500、花費才 23%；spend-cap 的 429 會明寫「exceeded its monthly spending cap」，我們是通用「Resource exhausted」無此字樣。
- **不是 store 壞掉**：`file_search_stores.get` → 2714 active / **0 failed** / 19.75MB（健康）。**受控實驗**（同窗交替查舊 store vs 新建 test store）→ **兩者同進同退**（一起成功/一起 429）→ 確認是**帳號層級**、非 store。
- **不是每日配額（RPD）**：Tier 1 embedding RPD = unlimited。
- **真因**：`gemini-embedding-001` 的 **server-side 區域速率/容量限流**（Google 自 2025 末承認、無 ETA；連 65 檔的小庫都有人中）。**被「今天 backfill 重嵌 2718 課」+「fan-out 一次 6-8 並行 query embedding」放大**。
- **可控緩解**（非根治，根治在 Google 端）：已有指數退避+jitter（官方第一優先）；**停止 burst** 是最有效（密集探針會加劇）；fan-out 限併發/快取可降觸發。**勿重建 store**（沒用、白燒）。

### ⏳ 收工時仍卡 embedding 限流、未做完的項目
- **#9 補 11 課**（store 2714/2718，~4 課待補）：要索引 = 要 embedding burst，限流中會更糟，**待冷卻或離峰再跑** `ONLY_FAILED=1 BACKFILL_STORE=...znuka50qq2y2 scripts/backfill_async.py`。
- **#20 fan-out FG5 完整壓測**：壓測本質是並行 burst → 一定觸 429 且污染量測，**待 embedding 穩定再跑**（latency 已單點實測 79s）。
- **#5 跨裝置完整 e2e**（含真實 recommend/qa）：靜態 UI 跨裝置可驗；含真實檢索的端到端待 embedding 穩。
- **#12 wait-fatigue 進度條決策**：本回合已做誠實 eta 校準；wip 分支最終取捨待使用者拍板。
- **SSE 斷線補救（已研究、待實作）**：LLM 串流無法廉價續傳（無 server buffer、Gemini 串流不可重播）→ 業界標準＝「斷線→改非串流 POST 重抓完整結果」（ChatGPT Regenerate、Claude continuation）。**現有 fallback 骨架已在**（app.js EventSource error→POST），待硬化：關自動重連、斷線顯示「重新取得中…」、POST 補完整、再失敗給重試鈕。
- **（已放棄）推薦「思考揭露」UX**：研究完（NN/g + `include_thoughts` 可行，技術＝`ThinkingConfig(include_thoughts=True)` 串流 `part.thought` 摘要）+ 做了 visual companion mockup，使用者決定不做。

### 設計決策補充（本回合新增）
- **fan-out 取捨**：延遲砍半，但**每請求 embedding 從 1→6-8 次**（放大花費 + 撞限流機率）——真實 trade-off。
- **同源部署**：root `railway.toml` 被所有 repo-connected service 讀到（官方：config 不跟 Root Directory 走）→ 改 backend StaticFiles 服務前端。
- **citation/思維鏈**：grounding 用結構化 `custom_metadata`；串流用 `part.thought` 過濾。

---

## ★ Session 3 交接（換機器接手必讀）★

> 分支 **`feat/streaming-ux`**（已 push）。串流 UX 已端到端跑通並真實 E2E 驗證；過程踩到並查清三個關鍵根因。

### 已完成且 E2E 驗證
- **問答(/qa)串流**：grounding 真檢索 → 真答案 + Markdown 表格 + citations + followup，無罐頭、無 JSON 洩漏。打字機（committed/live 分離防頻閃、尾端純文字逐字避免空窗）。
- **推薦(/recommend)串流**：5 階段 SSE 即時進度（understand/retrieve/filter/compose/finalize）+ 前端 stepper。
- **QA 等待 UX**：借鑑使用者自己的參考 bot（Albert Assistant @ web-production-40bdc.up.railway.app）——首 token 前在泡泡內顯示 **5 階段垂直 stepper**（理解問題→翻閱課綱→比對重點→整理段落→最後潤飾，done/active/pending + 細漸層進度條）+ spinner，首 token 到切打字機。
- 大量前端 bug 修復：拿掉巨大 spinner/進度條、矮視窗滾不到底、QA 縮成一塊、表格頻閃、副標題跑版、手機「+新對話」改 icon、QA 改穩固 flex chat 佈局。
- 後端健壯化（F3/F5 已合併）：qa_stream 斷線不寫半截 turn + 歷史過濾失敗輪；recommend_stream 補 logging/judge。
- 後端測試 95 passing。前端資源版本到 **?v=12**。

### 🔑 三個查清的根因（以下即完整版；memory 僅原機器本地有，新機器看這裡即可）
1. **grounding 對 prompt 極敏感**：`config.system_instruction` 的人設會讓 2.5-flash 跳過 file_search → grounding=0 → citations 空 → 防幻覺 override 誤觸發 → **罐頭答案**。解：system_instruction 最前面加「【鐵則・最高優先】回答前務必先檢索」（qa.py 已有，**不可移除**）。⚠️ **拿掉 JSON 包裝改純 Markdown 會破壞 grounding（實測 3/3=0），已回退**；no-JSON 要保 grounding 需 lean-prompt 重寫（未做）。
2. **429「high demand」真因＝SDK 預設 timeout 60s**：recommend 檢索/分組單次 ~80-100s > 60s → client 逾時、伺服器仍跑 → retry 送新請求/重新嵌入 → 分裂成多併發 → 燒爆 RPM → 429 風暴。解：`HttpOptions(timeout=180_000)`（main.py 已改）。**同把 key 下參考 bot 穩、我們爆的差別就在此（它快查詢只嵌入 1 次）**。
3. **embedding 429「Failed to embed content」**：`gemini-embedding-001` free tier 100 RPM。我們慢檢索+timeout 重試把同一問句嵌入多次 → 爆；參考 bot 快查詢只嵌入 1 次 → 不爆。timeout 修復後正常單一使用不會中（主要是密集測試燒的）。

### ⛔ 待辦（換機器回家做，依優先）
1. **★ metadata-drop 修復 port（高，會嚴重削弱資料）**：`join_metadata`(recommend.py:114) 靜默丟掉 LLM 抄錯 9 碼 course_id 的課 → topk 檢索 ~10 門最後只回 3 門。**修法已由 agent 做好**（name 索引修正 + 前 6 碼前綴復原 + 共用 build_groups helper + 14 測試），但 **agent fork 錯基底、只修了非串流的兩個 pipeline、缺 `stream_recommendation`**。已 push 分支 **`wip/recommend-courseid-recovery`**——回家 `git fetch` 後把該分支的 recommend.py 修法 port 到 feat/streaming-ux 的 `stream_recommendation`（也走 build_groups），跑 95+ 測試，live E2E（等 embedding 額度冷卻）。
2. **F1 等待疲勞修復決策**（中）：agent 做好了（ETA 校準 50→100s、區間措辭、retrieve 微動、超時文案、輪播擴充），但**為 retrieve 微動把進度條加回來了**——與你之前「拿掉推薦進度條」衝突，**需你決定要不要那條細進度條**。已 push 分支 **`wip/wait-fatigue-f1`**。
3. 其餘 F2（防自動重連重跑）、V（跨裝置/Sentry 驗收）、部署 D1-D6（見下）。

### 換機器須知
- `.env` gitignored，新機器要自建：`FILE_SEARCH_STORE_NAME=fileSearchStores/nccucourses1142-znuka50qq2y2`（full store 2714 docs），同一把 `GEMINI_API_KEY`，`ALLOWED_ORIGIN=*`（本機）。
- 本機後端起法：`DATABASE_URL=... ALLOWED_ORIGIN=* python -m uvicorn backend.main:app --port 8000 --host 127.0.0.1`；前端 `python -m http.server 3000`（frontend 目錄）。前端本機測試要把 `CONFIG.API_URL` 指到 `http://127.0.0.1:8000`（避免 localhost→::1）。
- 已 push 分支：`feat/streaming-ux`（主）、`wip/recommend-courseid-recovery`（metadata 修法待 port）、`wip/wait-fatigue-f1`（F1 待決策）。

---

## 零、最優先事項：推薦延遲提速方案抉擇（**未決，待做**）

> **使用者最在意的痛點：`/recommend` 太慢（~40-50s）。** 這是接手後的第一優先。
> 約束（使用者明確要求）：**生成模型必須維持 `gemini-2.5-flash`，不可換 flash-lite；thinking 不可關**（兩者都會犧牲品質：flash-lite 品質低約 10%、關 thinking 會讓檢索/分組稀疏，皆已實測）。

### 研究結論（已用 context7 + web search 查證）
- **RAG 延遲主因 = LLM 輸出 token 數**（逐字生成）。來源：[RAG Latency Optimization](https://apxml.com/courses/optimizing-rag-for-production/chapter-4-end-to-end-rag-performance/rag-latency-analysis-reduction)、[Echelon RAG breakdown](https://www.echelonedge.com/blogs/breaking-down-a-rag-pipeline-where-latency-really-comes-from/)。
- **查詢時不重索引**：File Search 只嵌入「問句」，文件嵌入是一次性（[File Search docs](https://ai.google.dev/gemini-api/docs/file-search)）。所以延遲不是來自重索引。
- **可控解法（排序）**：① 降輸出量 ② 減少 LLM 呼叫次數 ③ 小模型 ④ 精簡 prompt ⑤ 快取 ⑥ streaming（感知）。來源：[CompactRAG](https://arxiv.org/pdf/2602.05728)、[flash-lite benchmark](https://venturebeat.com/ai/googles-gemini-2-5-flash-lite-is-now-the-fastest-proprietary-model-and)。

### 已做（robust 基線，不犧牲品質）
- 降輸出量：`POOL_SIZE` 40→24、stage2 理由精簡（lead 25字/2 points/detail 20字）。
- thinking 維持自動（保品質）；client 加 **429/503 退避重試**（3×8s，抗瞬間爆量）。
- 前端**分步驟等待 UX**（NNgroup 感知優化：步驟進度+預估時間+進度條+輪播）。
- 真實延遲仍 ~40-50s（flash + 兩階段 RAG 本質下限）。

### ⭐ 待抉擇的兩個「保品質」提速方案（接手要做的第一件事）
- **方案 A：合併 stage1+stage2 為單次 file_search 呼叫** → 約砍半延遲（~20-25s）。
  - 做法：一次 generate_content(file_search) 直接輸出分組結果的自由文字 JSON（file_search 不能配 response_schema，故 parse 自由文字，類似 qa）。
  - **代價**：「換一批」多樣性目前靠 stage1→stage2 之間的 `sample_candidates`，合併後沒有中間候選池可抽 → 需改用 **prompt 變化（帶 seed/技能側重輪替）** 重做多樣性。
- **方案 B：預先算好 50 大職涯推薦並快取** → 熱門職涯瞬間回。
  - **代價**：要為每職涯預存**多組 seed 結果**才能保「換一批」多樣性；清單外職涯仍即時算。
- 折衷：A（架構提速）優先；B 作為熱門職涯的加速層。**建議先和使用者確認走哪個再動工。**

> ⚠️ 量測注意：先前 backfill burst + 連續測試把 embedding **速率上限打爆(429)**，導致延遲量測被重試灌水（同端點 9s~156s 亂跳）。乾淨量測需等速率冷卻，或在部署環境用正常流量驗。

---

## 一、目前狀態總覽

| 模組 | 程式碼 | 測試 | 真實端到端驗證 | 部署 |
|------|:---:|:---:|:---:|:---:|
| Ingestion（知識庫建置） | ✅+並行/健壯化 | ✅ 15 | ✅ 5課 dry-run | — |
| Backend 職涯推薦 | ✅ | ✅ | ✅ 真實 Gemini | 🟡 service FAILED 待修 |
| Backend 推薦多樣性（換一批/seed） | ✅ | ✅ | ⬜ 待 full store | ⬜ |
| Backend 清單外職涯（LLM推導+no_match） | ✅ | ✅ | ⬜ 待 full store | ⬜ |
| Backend Logging + Judge | ✅ | ✅ | ⬜ 待 live Postgres | ⬜ |
| Backend Q&A（markdown表格/粗體/離題引導/防幻覺） | ✅ | ✅ | ✅ live 後端探針 | ⬜ |
| Sentry 觀測（後端+前端，env驅動） | ✅ | ✅ | ⬜ 待設 DSN | ⬜ |
| Frontend（多樣性UI/markdown渲染/清單外/RWD） | ✅ | — | ✅ Playwright 注入樣本(mobile/tablet/desktop) | ⬜ |
| Railway Postgres | ✅ 已建+schema | — | ✅ schema 套用成功 | ✅ |

**測試總計：78 passing**（Session 1 起 49 → 新增 29）。

**SDK 變更**：本機新環境裝到 `google-genai 2.7.0`（非 1.68.0）。已修 qa.py 相容（interactions response 改 `steps`/`output_text`，extract 同時相容兩版）。
**本地驗證環境**：Railway Postgres（用 `DATABASE_PUBLIC_URL` 連，免裝 Docker）。
**✅ Full store 已建好**：`fileSearchStores/nccucourses1142-znuka50qq2y2`（**2713/2718 課**，11 課待補；用 `scripts/backfill_async.py` 高併發灌入，~30 分）。`.env` 與 Railway backend 的 `FILE_SEARCH_STORE_NAME` 已更新；`backend/courses_meta.json` 已 commit 2718 課版。
**目前狀態**：核心功能全部完成 + live 驗證（資料科學家/軟體工程師 真檢索、dedup 不重複、多樣性、Q&A markdown、Sentry 真捕捉前端錯誤）。**剩：①延遲提速抉擇(見「零」) ②git push 部署收尾 ③補 11 課 ④跨裝置真後端 E2E。**

---

## 二、進展史（時間序）

1. **Brainstorming → Spec**：兩份設計文件（推薦系統、Q&A 模式）於 `docs/superpowers/specs/`。
2. **研究確認資料源**：用 Playwright 探勘政大網站 → 確認 `CoursesList.xlsx`（2877課）+ 課綱 URL 公式 + SSL legacy 需求。
3. **Plan 1 Ingestion**（commits eb124dc→a692d9f）：xlsx_parser/scraper/skill_tagger/uploader/run.py。15 tests。5 課 dry-run 端到端成功，產生 dry-run store + courses_meta.json。**過程修 3 個 SDK bug**（create config、import_file poll、SSL）。
4. **Plan 2 Backend 推薦**（fe22adb→567db2d）：models/recommend/judge/main/Dockerfile。26 tests。真實 Gemini /recommend 驗證（公務員→3課分組）。**修 stage1 file_search+json_mime 衝突**。
5. **Plan 4 Logging+Judge**（b98fb97→3e966b4）：db/logger/judge + 兩階段背景 logging + analytics.sql。
6. **Plan 3 Frontend 推薦**（9454925）：navy glassmorphism SPA。桌面+mobile offcanvas Playwright 驗證。**修 `[hidden]` loading bug**。同時把後端 reason 改結構化（lead+points）。
7. **Q&A 模式**（2472970→0c49378）：
   - Spec（含 NNgroup 模式可見性、RAGAS judge、followup chips 研究）
   - 並行實作（dispatch 後端 agent + 主線做前端）：qa/qa_judge/qa_logger + 雙模式前端 + main.py 整合
   - **修 interactions API outputs 解析 bug**（扁平 list、annotation source 萃 course_id）
   - 真實驗證：單輪✅ 多輪✅（previous_interaction_id 上下文接續）citations✅ followup✅

---

## 三、WBS（工作分解 + 狀態）

### ✅ 已完成

- [x] 1. Ingestion pipeline（程式碼 + 15 tests + dry-run）
- [x] 2. Backend 推薦 API（/recommend，26 tests，真實驗證）
- [x] 3. Backend Logging + 推薦 Judge（程式碼 + tests）
- [x] 4. Backend Q&A（/qa /qa/session，qa tests，真實單輪+多輪驗證）
- [x] 5. Frontend 推薦模式（桌面+mobile Playwright）
- [x] 6. Frontend Q&A 模式（雙模式 UI 程式碼完成）
- [x] 7. 後端整合（main.py /qa endpoint + models）
- [x] 8. CLAUDE.md + HANDOFF.md
- [x] 9. **Frontend Q&A 桌面端到端 Playwright 驗證**（2026-06-03）
  - 單輪：問題氣泡→loading「AI 正在查閱課綱」→答案氣泡（4 段）+ 參考課綱 citation 連結 + followup chips（3）✅
  - 多輪：點 followup chip 追問，第 2 輪答案有上下文接續（正確區分政治系必修 vs 經濟/社會系），5 citations，狀態列「第 2 輪對話」✅
  - 新對話：清空 + 回 empty state + session label 歸位✅
  - 模式切換不污染：切推薦再切回，對話完整保留，視覺乾淨（a11y 樹的 `↗` 是 Chromium 對 hidden `::before` 的誤報，非真實洩漏）✅
  - DB 持久化：qa_session.turn_count=2 + last_interaction_id；qa_turn 兩筆 success，citation_count 1/5，latency 16.7s/21.2s✅
  - 已知：背景 RAGAS judge 此次因 Gemini 503（模型過載）未回填分數，但 /qa 仍回 200、優雅捕捉不阻塞（設計預期）

### 🟡 進行中

- [ ] 13. **Railway 部署**（已起步，卡在 #11 full ingestion store）
  - ✅ Railway CLI 登入（albertpeng678@gmail.com）
  - ✅ Railway 專案 `NCCU-POC` 已由使用者建立並 `railway link`（environment=production），目前**空專案無 service**
  - ✅ 決策定案：(a) service 由 Claude 用 CLI 建（`railway add`/`railway up`）；(b) store 走 **full ingestion**（option 2，非 dry-run）；(c) **部署一定走 git push**（GitHub-connected service，非 `railway up` 本地上傳）
  - ✅ frontend 靜態部署檔已建並 push 到 master：`frontend/Dockerfile`（nginx:1.27-alpine + envsubst $PORT）+ `frontend/nginx.conf.template`，本地 docker 驗證 200
  - ⛔ **卡點**：backend 的 `FILE_SEARCH_STORE_NAME` 需 full ingestion(#11) 產生的新 store，未跑完無法填 → 部署收尾受阻
  - 待做（回家後）：跑 #11 → 取新 store name → 建 Postgres+backend+frontend service（git-connected）→ 設 env → 部署 → 改 app.js API_URL → 收緊 CORS（見「四、部署順序」）

### ⬜ 待完成（依優先序）

- [ ] 11. **Full ingestion run**（2877 課）★ #13 的前置阻塞
  - 指令：`cd C:/side/NCCU-poc && python -m ingestion.run`（不帶 INGESTION_LIMIT）
  - 耗時 30-90 分，消耗 Gemini quota，free tier 可能撞 rate limit
  - 完成後更新 `.env` 與 Railway 的 `FILE_SEARCH_STORE_NAME`，並 commit 新 courses_meta.json
- [ ] 10. **剩餘推薦模式 E2E**（Plan 3 task #36/#37）
  - tablet 768 響應式截圖
  - 鍵盤導航（Tab/方向鍵/Enter/Esc）
  - 錯誤狀態（503 顯示）
- [ ] 14. **Q&A 模式跨裝置 E2E**（mobile/tablet 對話氣泡、composer）
- [ ] 15.（可選）Q&A 重整讀回 GET /qa/session 前端串接（目前後端有，前端未串）

---

## 四、Railway 部署順序（避免環境變數 chicken-and-egg）

> **部署方式定案：走 git push（GitHub-connected service），非 `railway up` 本地上傳。**
> service 連結到 repo `albertpeng678/NCCU-POC`，push master 觸發 build。
> ⚠️ git-connected service 首次需在 Railway dashboard 授權 Railway GitHub App 存取此 repo（OAuth，dashboard 操作）。
> 專案 `NCCU-POC` 已建並 link；frontend service 的 root directory 設為 `frontend/`（用 `frontend/Dockerfile`）。

1. 本地跑 `python -m ingestion.run` → 取得新 `FILE_SEARCH_STORE_NAME`（full 版，非 dry-run）
2. Railway 加 Postgres service → 跑 `backend/schema.sql` → 取得 `DATABASE_URL`（用變數引用注入 backend）
3. 建 backend service（連 repo，root=repo 根，用 `railway.toml`/`backend/Dockerfile`）：設 env `GEMINI_API_KEY`/`FILE_SEARCH_STORE_NAME`/`DATABASE_URL`/`ALLOWED_ORIGIN=*` → push 部署 → 取得 backend URL
4. 把 backend URL 填入 `frontend/app.js` 的 `CONFIG.API_URL` → commit + push
5. 建 frontend service（連 repo，root=`frontend/`，用 `frontend/Dockerfile`）→ push 部署 → 取得 frontend URL
6. backend `ALLOWED_ORIGIN` 改為 frontend URL → redeploy

---

## 五、新機器從零設定（git clone 後完整步驟）

> 適用：在另一台機器 clone 此 repo 後，要跑起本地驗證環境。
> repo 已含 `backend/courses_meta.json`（5 課 dry-run 版）。**未含** `.env`（gitignored）、Python 套件、Postgres。

### 前置需求
- Python 3.11+、Docker、git
- **同一把 `GEMINI_API_KEY`**（dry-run File Search Store 綁在原帳號雲端，換 key 就存取不到，會回 503）

### 步驟

```bash
# 0. clone（用 github-personal key）
git clone git@github.com:albertpeng678/NCCU-POC.git && cd NCCU-POC

# 1. 安裝依賴
pip install -r backend/requirements.txt
pip install -r ingestion/requirements.txt   # 若要跑 ingestion

# 2. 建 .env（從 example 複製後填值）
cp .env.example .env
#   填入：
#   GEMINI_API_KEY=<與原機器同一把，才能存取既有 dry-run store>
#   FILE_SEARCH_STORE_NAME=fileSearchStores/nccucourses1142-1ie5gitqgtur   # dry-run 5課；full run 後換新值
#   ALLOWED_ORIGIN=*

# 3. 本地 Postgres（容器名自取，埠 5440 與下方一致即可）
docker run -d --name nccu-pg -e POSTGRES_PASSWORD=nccu -e POSTGRES_DB=nccu -p 5440:5432 postgres:16-alpine
#   等就緒後套 schema：
docker exec -i nccu-pg psql -U postgres -d nccu < backend/schema.sql

# 4. 全測試（不需 DB/Gemini，純單元測試）
python -m pytest tests/ -q          # 預期 49 passed

# 5. 啟動 backend（DATABASE_URL 用環境變數傳，勿寫進 .env 以免覆蓋部署設定）
DATABASE_URL="postgresql://postgres:nccu@127.0.0.1:5440/nccu" python -m uvicorn backend.main:app --port 8000
#   健康檢查：curl http://localhost:8000/health → {"status":"ok"}

# 6. 啟動 frontend
cd frontend && python -m http.server 3000
#   開 http://localhost:3000/index.html
```

### 驗證可用性（dry-run store 限政治/社會/經濟相關）
```bash
# 推薦模式（會 match 的職涯）
curl -s -X POST http://localhost:8000/recommend -H "Content-Type: application/json" \
  --data-binary "{\"career\": \"公務員\"}"
# 問答模式
curl -s -X POST http://localhost:8000/qa -H "Content-Type: application/json" \
  --data-binary "{\"question\": \"政治學在教什麼?\", \"session_id\": null}"
```

> ⚠️ 新機器若 **沒有原 GEMINI_API_KEY**：無法用既有 dry-run store。需自己跑一次 ingestion 建新 store（見待辦 #11），或向原作者取得 key。
> ⚠️ Windows 注意：連 Docker Postgres 用 `127.0.0.1`（非 `localhost`，避免 IPv6 `::1` 連線被拒）。

---

## 六、已知限制 / 注意事項

- **dry-run store 只有 5 課**：問答/推薦只在政治/社會/經濟相關問題有結果；其他職涯（如資料科學家）會回 503「無候選課程」。Full ingestion 後才完整。
- **File Search Store 是 Gemini 帳號層級雲端資源**（非本機檔案）：同一把 `GEMINI_API_KEY` 在任何機器都能存取同一個 store，故跨機器驗證只需 .env 填對 key + store name，無需重新 ingestion。換 key = 看不到既有 store。
- **/qa 強依賴 DB**：無 DATABASE_URL 時回 503（與 /recommend 不同，後者可優雅降級）。
- **latency**：兩階段推薦 ~50-80s，問答 ~15-30s（Gemini File Search 本身較慢）。PoC 可接受。
- **courses_meta.json 已 commit**（un-gitignored）：backend runtime 依賴，Railway 從 git build 需要它。
- **caveman hook 已從 ~/.claude/settings.json 移除**（曾導致 Windows 卡頓 + 輸出雜訊）。

---

## 七、Session 2 進度（2026-06-04，分支 `feat/poc-enhancements`）

### 已完成並 commit（皆有 Playwright/測試驗證）
1. **Q&A 強化**（`b7c3f39`）：system_instruction（角色+主題邊界/離題溫和引導+輸出格式）；回答=適量文字+（列課程時）markdown 表格+**克制**粗體；格式驗證+1次重試；citations 空時覆寫「查無資料」防幻覺；**修 google-genai 2.7 回應結構相容 bug**（舊讀 `.outputs` → 改 `extract_answer_text`/`steps`，雙版相容）。
2. **推薦多樣性**（`8201395`/`d7b3347`/`3e81434`/`6f8a3c6`）：stage1 池擴 40 + `sample_candidates`（定錨4+seed 隨機抽18）→ 同職涯每次不同但相關；`/recommend` 支援 seed；前端「換一批」按鈕 + 導問答 CTA。spec/plan 在 docs/superpowers。
3. **清單外職涯**（`975b518`/`662bd08`/`c9ae36d`/`19adaf1`）：`derive_skills_for_career`（LLM 推可轉移技能）；stage1 誠實回空；`/recommend` 不再 400 → notice（真實檢索的可轉移課）或 no_match（導問答）；前端放寬送出 + notice 條 + no_match 卡。**保證課程卡皆真實檢索、不捏造**。
4. **Sentry**（`70b7013`）：後端 sentry-sdk[fastapi]（SENTRY_DSN env 驅動，未設停用）+ 例外吞點 capture_exception；前端 @sentry/browser CDN + CONFIG.SENTRY_DSN。**啟用需建 Sentry 專案填 DSN**（org=albert-ar）。
5. **前端 RWD**（`8612e88`）：tablet 改 2 欄（mobile 1/tablet 2/desktop 3）。
6. **Ingestion 健壯化**：`f86ef65` 加 client timeout（防單呼叫 hung）；`0a60818` skill bridges/upload 改 ThreadPoolExecutor 並行；`8c0cfe0` 上傳降併發+多輪重試+import timeout 240s+`docs_cache.jsonl`（可續跑）。
7. **Railway Postgres**：已建 service + 套 schema.sql（用 `DATABASE_PUBLIC_URL` 本機連）。
8. **前端 RWD/UX**：tablet 2 欄（`8612e88`）；**等待 UX**（分步驟+進度條+輪播，`?v=N` cache-bust，showLoading 防禦）；換一批 emoji 換 SVG。
9. **多樣性 bug 修復**（`c3884ee`）：`deduplicate_by_name` 修「換一批出同名重複課」；conftest 測試停用 Sentry。
10. **Sentry 啟用**（`f007db1`）：建好專案 `nccu-poc`（org albert-ar）+ 前端填 DSN（已驗證 client 啟用）+ Railway backend 設 `SENTRY_DSN`。**Sentry 已實測捕捉到真實前端錯誤並寄警示信。**
11. **Full store 灌好**（`backfill_async.py`）+ `.env`/Railway 的 `FILE_SEARCH_STORE_NAME` 更新 + `courses_meta.json` 2718 commit（`7af466d`）。
12. **延遲 robust 配置**（`af1575f`、`6dff462`）：降輸出量 + 429/503 重試 + 生成還原 flash（見「零」）。
13. **live AC（真後端+full store）**：資料科學家→10門真實資料科學課、軟體工程師→9門 0 重複（dedup 生效）。換一批/清單外「記者/流浪漢」測試因 429 速率牆中斷，待冷卻後補驗。

### ⛔ Full store 卡點 + 復原（重要）
- full ingestion 兩次卡關：(a) 並行 x8 灌 `import_file` → store 索引佇列雪崩(2578/2718 timeout，只進 141 課)；(b) 之後撞 **Gemini 月度 spend cap → 全 429**。
- **根因**：`import_file` 不耐高併發（用低併發 3-4 + 240s timeout）；spend cap 需 ai.studio/spend 調高。
- **復原**：scrape+skill_bridge 已快取於 `ingestion/docs_cache.jsonl`（2718 筆）。cap 解除後跑 backfill 從快取灌入（不重爬/重標）。
- **⚡ 上傳效率解法（已實測）**：`scripts/backfill_async.py` 用 **async client + `upload_to_file_search_store`（一步上傳+索引）+ semaphore 高併發(16)**，實測 **~80-101 課/分鐘、0 失敗**（vs 舊版兩步+ThreadPool x3 僅 12/min 且高併發會雪崩）。全 2718 課約 **25-35 分鐘**。根因：慢與雪崩來自「併發太低 + 兩步流程 + 60s timeout 太短」，**非** File Search 伺服器吞吐天花板。→ **`ingestion/run.py` 未來應改用此 async 一步法**（目前 run.py 仍是 ThreadPool 兩步版）。
  指令：`CONCURRENCY=16 .venv/bin/python scripts/backfill_async.py`（`LIMIT=N` 測試、`ONLY_FAILED=1` 只補失敗、`BACKFILL_STORE=...` 續灌既有 store）。

### 待辦（接手點，依優先序）
0. **★ 推薦延遲提速方案抉擇**（見「零、最優先事項」）—— 使用者最在意，先和使用者確認走方案 A（合併呼叫）或 B（快取）再動工。
1. **部署收尾**（走 git push GitHub service）：
   - backend service `NCCU-POC` env **已設好**（FILE_SEARCH_STORE_NAME新store / GEMINI_API_KEY / `DATABASE_URL=${{Postgres.DATABASE_URL}}` / SENTRY_DSN / ALLOWED_ORIGIN=*）。
   - **把 `feat/poc-enhancements` 的程式推上 GitHub**（Railway 從 GitHub build）→ 確認 backend 部署 /health 過。
   - 取 backend public URL → 填 `frontend/app.js` `CONFIG.API_URL` → commit/push → 建 frontend service（root=`frontend/`）。
   - frontend URL 出來後 backend `ALLOWED_ORIGIN` 收緊成該 URL → redeploy。
2. **補 11 課**：`ONLY_FAILED=1 BACKFILL_STORE=fileSearchStores/nccucourses1142-znuka50qq2y2 .venv/bin/python scripts/backfill_async.py`（讀 `ingestion/backfill_failed.json`）；若仍頑固失敗，診斷那幾筆文件（可能空內容）。
3. **跨裝置真後端 E2E**（Playwright 5x）：換一批真換不同課、清單外職涯（記者/流浪漢）、mobile/tablet 對話氣泡。先前因 429 速率牆中斷。
4.（可選）`ingestion/run.py` 改用 async 一步上傳法（目前 backfill_async.py 才有；run.py 仍是慢的兩步版）。
5.（可選）Q&A 重整讀回 GET /qa/session 前端串接。

### 換機器接手（另一台電腦）
1. `git clone` 後 checkout `feat/poc-enhancements`（或已 merge 的 master）。
2. 建 `.env`：`GEMINI_API_KEY`（同一把）、`FILE_SEARCH_STORE_NAME=fileSearchStores/nccucourses1142-znuka50qq2y2`、`SENTRY_DSN`（見 app.js 同一個）、`ALLOWED_ORIGIN=*`。
3. `pip install -r backend/requirements.txt`（含 sentry-sdk）；本機 /qa 用 Railway `DATABASE_PUBLIC_URL` 當 `DATABASE_URL` 環境變數傳入。
4. 起 backend：`ALLOWED_ORIGIN=* DATABASE_URL="<Railway public url>" python -m uvicorn backend.main:app --port 8000`；起 frontend：`python -m http.server 3000 --directory frontend`。
