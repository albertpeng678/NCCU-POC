# 檢索層遷移 Gemini → OpenAI 設計文件（spec）

> 日期：2026-06-08。作者：Claude（brainstorming）+ albertpeng678。
> 狀態：設計定案，待 review → 進 writing-plans。

---

## Goal（一句話）

把課程推薦 PoC 的**檢索層與生成層整套從 Gemini 遷移到 OpenAI**，根治 Gemini 託管 RAG 的不穩（`gemini-embedding-001` 區域限流 429、`gemini-2.5/3.5-flash` 503 high-demand），同時保住現有的三大檢索能力：①多輪問答記憶 ②out-of-scope 處理 ③grounding/citation 對回課綱。

## 動機

Gemini 反覆不穩，且不可控（Google server-side 區域限流、新模型 GA 上線潮 503、無 ETA）。OpenAI 託管 file_search 是 GA 付費服務，計費為**儲存導向**（非耗用式 embedding 額度），對小語料近乎免費，且 citation/grounding 內建。詳見同目錄六路調研結論（codebase 盤點 / 託管 RAG / 自架 / 對話品質 / 生成穩定性 / 開源 repo）。

## 決策摘要（已與使用者逐項確認、鎖定）

| 項目 | 決策 | 備註 |
|---|---|---|
| 供應商 | **OpenAI**（檢索 + 生成都換） | 使用者已有 API key |
| 切換策略 | **兩模式都硬切、不留 Gemini、不做回退抽象** | 安全網 = 上線前 e2e 黃金集 gate（無 flag，回退靠 git revert + 重部署） |
| 知識庫 | **OpenAI Vector Stores**（託管，embedding 為 OpenAI 內部 text-embedding-3，不可挑） | 混合語意+關鍵字檢索 |
| 推薦檢索 | **直接 `vector_stores.search` 端點**，每技能一支 fan-out | 官方確認**不按次收費**（只算儲存，我們免費） |
| 問答檢索 | **Responses API + `file_search` tool** | 內建 annotations citation、模型看歷史自己改寫檢索 query |
| 多輪記憶 | **沿用 Postgres `qa_session`/`qa_turn`**，每次傳最近 N 輪 | 供應商中立，不綁 `previous_response_id` |
| out-of-scope | **`ranking_options.score_threshold` + citation 空覆寫「查無資料」** | 用程式碼判分數，**不塞 system prompt**（避免破壞 grounding） |
| 生成型號 | **`gpt-5.4-mini`（兩模式統一）** | `gpt-5.5-mini` 不存在；5.4 mini 是最新 mini、支援 file search、$0.75/$4.50、400K context |
| citation 對應鍵 | **檔名 = course_id**（如 `070415001.txt`）+ 檔案 attributes 雙保險 | annotation 回 filename → 去副檔名 → course_id → `courses_meta.json` |
| 分階段 | Phase 0 建庫+smoke → Phase 1 推薦 → Phase 2 問答 → Phase 3 清理 | 每階段 e2e 5x gate |

## 不在範圍（Non-goals）

- 不換向量庫到自架 pgvector（OpenAI 託管已夠穩、近免費、citation 內建；自架 pgvector 是「若想完全脫離供應商」的備案，本次不做）。
- 不引入 LlamaIndex/LangChain 等重框架（會逼重寫已成形的 pipeline）。
- 不做 Gemini ↔ OpenAI 可切換抽象層（硬切）。
- 不動前端 UI 行為（推薦卡片、問答氣泡、串流打字機、柴犬等候、分頁換一批維持原樣；只換底層資料來源）。
- 不重爬 NCCU（語料已快取於 `ingestion/docs_cache.jsonl`）。

---

## 架構總覽

### Gemini → OpenAI 能力對應

| 現況（Gemini） | 遷移後（OpenAI） |
|---|---|
| File Search Store（託管 chunk+embed+檢索+grounding） | Vector Stores（託管，embedding 內部 text-embedding-3） |
| `FileSearch` tool 於 `generate_content` | 推薦：`vector_stores.search`；問答：Responses `file_search` tool |
| citation via `grounding_chunks[].custom_metadata.course_id` | annotation `file_citation.file_id`/`filename`（= course_id）→ `courses_meta.json` |
| 生成 `gemini-2.5-flash` | `gpt-5.4-mini` |
| 多輪 history（已自建，存 Postgres） | **不變**，傳最近 N 輪給 Responses |
| 思維鏈 `part.thought` 過濾 | 不需要（OpenAI 不外洩 reasoning 到 message） |

### 資料流

```
推薦：career → skills(career_skills.json 或 derive_skills) 
      → [vector_stores.search ×N skills, top_k=8, score_threshold] 
      → 合併池（dedup 前6碼 + 課名）→ 1 次 gpt-5.4-mini 標註(structured) → 排序分桶
      → （career_budget 命中則直接讀 Postgres，0 即時 AI；維持現狀）

問答：question + Postgres 最近 N 輪 
      → responses.create(gpt-5.4-mini, tools=[file_search top_k=5 score_threshold]) 
      → 答案 + annotations → filename(course_id) → courses_meta citation
      → out-of-scope 判定（無命中/分數低 → 查無資料）+ 防幻覺後驗（剝不在 meta 的假課）
      → 串流 token 回前端（漸進 markdown 維持）
```

---

## 元件設計

### 1. 資料層 / 建庫（Phase 0）

**輸入**：`ingestion/docs_cache.jsonl`（2718 筆 `{course_id, doc_text, syllabus_url}`，已存在）。

**新腳本 `scripts/build_openai_vector_store.py`**：
1. `client.vector_stores.create(name="nccu-courses-1142")` → 得 `vector_store_id`。
2. 每門課轉成一個檔案物件：**檔名 `{course_id}.txt`**、內容 = `doc_text`。
3. 上傳檔案（`client.files.create(purpose="assistants")` 或 upload helper），收集 file_id。
4. 用 **file batches** 把檔案掛進 vector store，**附 `attributes={"course_id":..., "syllabus_url":...}`**；2718 檔切 **2 批**（每批 ≤2000，避開 300 req/min 限制）。
5. Poll batch 狀態到 `completed`，印出 active/failed 數。
6. 輸出 `vector_store_id` → 填入 env `OPENAI_VECTOR_STORE_ID`。

**冪等**：可重跑（`ONLY_MISSING` 比對已存在 course_id），失敗清單可重補。一次性、庫 <1GB → 儲存免費。

**繁中 smoke test**（同階段、上線前 gate 的一部分）：拿 5–10 題真實課綱 query（涵蓋資工/社科/商管/法律）打 `vector_stores.search`，人工確認 top 結果課程相關、course_id 對得上。不對則調 `max_num_results` / `score_threshold`。

### 2. OpenAI client + 檢索模組

**新檔 `backend/openai_client.py`**：在 FastAPI **startup（lifespan）建立 `AsyncOpenAI` singleton**，同一 event loop 內使用。
> ⚠️ 教訓（HANDOFF Session 8）：**禁止 module-level import 時建 client、禁止跨 event loop 重用**。比照 asyncpg pool 在 startup 建、同 loop 用。

**新檔 `backend/retrieval_openai.py`**：
- `async def search_skill(vs_id, query, top_k=8, score_threshold) -> list[Chunk]`：呼叫 `vector_stores.search`，回正規化結果 `{course_id, score, content}`（course_id 取自 attributes，缺則由 filename 去副檔名）。
- `def course_ids_from_annotations(annotations) -> list[str]`：問答用，從 Responses annotations 的 `file_citation.filename`（= `{course_id}.txt`）萃 course_id；對應現 `extract_grounding_course_ids` 的角色。

### 3. 推薦模式（Phase 1）

改 `backend/recommend.py`：
- `fanout_retrieve_async`：把每技能的 Gemini `generate_content(FileSearch)` 換成 `retrieval_openai.search_skill(...)`（每技能一支，asyncio.gather 容錯）→ 合併池（沿用 `merge_fanout_results` round-robin + `deduplicate_by_prefix` + `deduplicate_by_name`）。
- `stage2_annotate_pool_async`：把整池標註的 Gemini 呼叫換成 `gpt-5.4-mini` 結構化輸出（沿用現有 `_RankedOutput` schema → `build_ranked_courses`）。OpenAI 走 `responses.create` 或 `chat.completions` 的 structured output（`response_format` json schema）。
- `derive_skills_for_career`（清單外職涯）：換 `gpt-5.4-mini`。
- `build_recommendation_instrumented_async`（Session 8 已是 async）+ `stream_recommendation`：底層改呼叫上述；**對外介面/回傳形狀不變**（前端、career_budget 不動）。
- **career_budget 維持**：命中 Postgres 直接秒回（0 即時 AI）；`scripts/build_career_budget.py` 改用新 OpenAI pipeline 離線重算 100 職涯。

### 4. 問答模式（Phase 2）

改 `backend/qa.py`：
- 檢索+生成：`responses.create(model="gpt-5.4-mini", input=[...], tools=[{type:"file_search", vector_store_ids:[vs], max_num_results:5, ranking_options:{score_threshold}}], stream=True)`。
- **多輪 input 組裝**：沿用 `build_qa_contents`/`build_history_from_turns`，把 Postgres 最近 `_MAX_HISTORY_TURNS` 輪轉成 Responses `input` 的對話陣列；system 段保留現「先檢索鐵則」（設計決策 #15，**不可移除**，但改寫成 retrieve-then-read 語境下的「只依檢索內容作答」）。
- **citation**：從 annotations 萃 course_id（`course_ids_from_annotations`）→ `courses_meta.json` enrich（沿用 `extract_citations` 後段）。
- **out-of-scope**：file_search 無命中 / annotations 空 / 分數低於門檻 → 回「課綱查無相關資料，換個問法？」**不採信模型自由文字**；沿用 `should_override_no_results` 精神但改判 OpenAI 訊號。
- **防幻覺**：沿用 `strip_fabricated_courses`（剝答案中不在 `courses_meta.json` 的假課名/課號）。
- **串流**：Responses streaming 逐 token → 前端漸進 markdown（`progressive-md.js`）維持；citation/followup 於 done 權威覆蓋。
- 清掉 Gemini 專屬死碼：`answer_question`（Interactions）、`_visible_text_from_chunk`（thought 過濾）、雙 SDK 形狀相容碼、`google.genai._interactions` 私有 import。

### 5. main.py / 設定

- client：`_client` 由 google-genai 換成 `AsyncOpenAI` singleton（startup 建）。
- env：新增 `OPENAI_API_KEY`、`OPENAI_VECTOR_STORE_ID`、`OPENAI_MODEL`（預設 `gpt-5.4-mini`）；移除/停用 `FILE_SEARCH_STORE_NAME`、`GEMINI_API_KEY`（Phase 3）。
- `/health` 外露 `retrieval_backend="openai"` + `model`（沿用 Session 9「可觀測、不釘隱形 env」原則）。
- retry：OpenAI SDK 內建 retry（`max_retries`）；保留逾時設定。

### 6. career_budget

不變（命中讀 Postgres 秒回）。離線重算腳本改用 OpenAI pipeline。Phase 1 完成後重灌 100 職涯。

---

## 成本

| 項目 | 成本 |
|---|---|
| 向量庫儲存 | $0（庫 <1GB，首 1GB 免費） |
| 建庫嵌入 | 含於儲存、不另計、不耗用額度（與 Gemini 相反） |
| 推薦檢索（`vector_stores.search`） | **$0/次**（不按次收費） |
| 推薦標註生成 | `gpt-5.4-mini` token（每請求一次，短輸出） |
| 問答檢索（file_search tool） | **$2.50 / 1000 次** tool call |
| 問答生成 | `gpt-5.4-mini` token（$0.75 進 / $4.50 出 每百萬字） |

PoC 流量下量級「月數美元內」，與現況相近但**穩定**。

## 不可退讓約束（遷移踩雷點，務必保留）

1. **course_id 是 chunk ↔ 課綱的唯一鍵**（設計決策 #19）：每檔檔名=course_id + attributes 雙掛；citation 萃取只信結構化來源，不掃答案文字。
2. **grounding 鐵則**（#15）：system「只依檢索內容作答、不腦補課名」不可拿掉，但需在 retrieve-then-read 語境重新表達並實測。
3. **out-of-scope 用程式碼判分數**，不要叫 system prompt 自己判。
4. **course_id 救援邏輯保留**（`correct_candidate_ids` 用課名修、`deduplicate_by_prefix` 前6碼、前綴復原）——與供應商無關，照留。
5. **top_k 量級**：問答 5、推薦每技能 8（維持，避免 citation 數/答案長度漂移）。
6. **AsyncOpenAI client 在 startup 建、同 loop 用**（Session 8 event-loop 教訓）。
7. **前端零行為改動**（只換資料來源）。

## 錯誤處理

- OpenAI 429/5xx：SDK 內建退避重試；超出則對使用者回友善錯誤（沿用 `classify_qa_error` 分類 → 前端忙線重試泡泡）。
- 建庫 batch 失敗：重補清單。
- 檢索空池：推薦回 `no_match`（沿用）；問答回「查無資料」。

---

## 分階段上線 + 驗收 gate（每階段 e2e 5x consecutive 0 flake 才算 GREEN）

- **Phase 0**：建庫腳本 + 灌 2718 課 + 繁中 smoke test（5–10 題人工確認召回）。
- **Phase 1（推薦）**：fan-out 改 OpenAI + 標註改 gpt-5.4-mini。
  - e2e（真後端 Playwright）：選清單內職涯（PM/資料科學家）→ 卡片渲染、course_id 對得上 `courses_meta`、分桶正常；選清單外職涯（記者）→ derive→檢索→出相關課或優雅 no_match。**5x**。
- **Phase 2（問答）**：Responses + file_search + Postgres 歷史 + 分數門檻 + citation 對應。
  - e2e：①多輪「想當PM修什麼」→「那金融呢」正確脈絡化（金融相關課）②離題題（問天氣）→「查無資料」不幻覺 ③一般題 citation 連結對回真實課綱 ④0 console error。**5x**。
  - 黃金集 ~30 題（10 多輪 + 10 離題 + 10 一般）跑現有 `qa_judge` + 拒答正確率。
- **Phase 3（清理）**：移除 Gemini 死碼 + google-genai 依賴 + 舊 env；`/health` 確認 `retrieval_backend=openai`。

安全網：硬切無 flag，故**每階段上線前 e2e gate 是唯一把關**；回退 = git revert + 重部署。

## 測試策略

- **單元（TDD red→green）**：`retrieval_openai`（search 正規化、annotation→course_id 萃取、空結果）、citation 對應、out-of-scope 判定、推薦標註 schema 解析、build 腳本 `select_courses`。mock OpenAI client 回傳（**只 mock 外部供應商，不 mock 自家 pipeline**）。
- **整合/e2e（Playwright 真後端）**：如上 Phase 1/2 的 5x gate；real data、禁 mock 自家 API、禁 stub timestamp。
- 回歸：全後端 pytest 維持綠。

## 要新增 / 修改的檔案

**新增**：
- `scripts/build_openai_vector_store.py`（建庫/灌庫，force-add）
- `backend/openai_client.py`（AsyncOpenAI singleton + startup）
- `backend/retrieval_openai.py`（search 包裝 + citation 對應）
- `tests/backend/test_retrieval_openai.py`、`test_qa_openai_citation.py`、`test_recommend_openai.py`、`test_build_openai_store.py`

**修改**：
- `backend/recommend.py`（fanout/stage2/derive 改 OpenAI）
- `backend/qa.py`（Responses + file_search + citation + OOS；清 Gemini 死碼）
- `backend/main.py`（client、env、/health）
- `backend/judge.py` / `backend/qa_judge.py`（judge 生成換 gpt-5.4-mini）
- `scripts/build_career_budget.py`（離線重算改 OpenAI）
- `backend/requirements.txt`（加 `openai`；Phase 3 移除 `google-genai`）
- `.env.example` / CLAUDE.md / HANDOFF.md（env 與設計決策更新）

**Phase 3 移除**：Gemini file_search 呼叫、`answer_question`、雙 SDK 相容碼、`ingestion/uploader.py` 的 Gemini 上傳（建庫改新腳本）。

## 風險

| 風險 | 緩解 |
|---|---|
| 繁中召回不如 Gemini（embedding 不可挑） | Phase 0 smoke test 先驗；不行則調 top_k/threshold 或評估 hybrid_search 參數 |
| 問答 grounding 行為與 Gemini 不同 | retrieve-then-read 語境重寫 system；Phase 2 黃金集實測 faithfulness |
| out-of-scope 門檻校準（太鬆漏幻覺/太緊誤拒） | 用黃金集調 `score_threshold` cutoff |
| 硬切無回退 | e2e 5x gate 為唯一把關；git revert 為最終退路 |
| 直接 search 端點計費若日後改變 | 目前官方確認免費；上線後留意帳單 |

---

## 執行治理（process governance，使用者硬要求）

> 此遷移為「極端複雜」工作，治理從嚴。以下為 release 的不可繞過條件。

### A. 三審 release gate（frontend / backend / db auditor 全數通過才 release）

| auditor | 審查範圍 |
|---|---|
| **backend** | OpenAI 串接正確性、`AsyncOpenAI` event-loop 安全（startup 建、同 loop 用）、retry/逾時/錯誤處理、推薦+問答 pipeline 邏輯、grounding 鐵則（#15）保留、out-of-scope 判定正確 |
| **db** | Postgres 多輪歷史（`qa_session`/`qa_turn`）串接、`career_budget` 重建、**course_id 對應鍵完整性**、asyncpg pool 用法、資料無漏/無重 |
| **frontend** | **前端零行為改動**驗證（推薦卡片/問答氣泡/串流打字機/柴犬等候/分頁換一批不變）、Playwright e2e 證據、跨裝置（mobile/tablet/desktop） |

- 三審 **並行 dispatch**（superpowers:dispatching-parallel-agents），用 superpowers:requesting-code-review 模板；任一不通過 → superpowers:receiving-code-review 退修 → 重審該維度。
- **全數通過才 release**。對應 RITUAL：Director（Opus）cold-read 三審結論 + e2e PNG 證據。

### B. MCP 實證 mandate（盡可能重現實際情況）

- **Playwright MCP**：每階段 e2e 真實重現「user action → DB persist → reload → visible」整段，**5x consecutive 0 flake 才算 GREEN**。**動用 Playwright 前必先徹底熟讀 `playwright-skill`**（locator 紀律、禁 mock 自家 backend success path、禁 `waitForTimeout`、Pitfall 11/14/18/19/3、多階段用 `test.step()`）。
- **Sentry MCP**：上線後查無新錯誤類別；主動重現真實失敗條件（OpenAI 429/5xx/timeout）確認降噪、重試、友善錯誤泡泡生效。

### C. superpowers skill → 工作環節對應（每個都實施，N/A 誠實標註）

| skill | 環節 |
|---|---|
| using-superpowers | 全程：每步先檢查該用哪個 skill |
| brainstorming | ✅ 本 spec |
| writing-plans | 下一步：spec → 逐步計畫（每步含 fail test + 驗證指令） |
| subagent-driven-development | 實作主軸：每 task 派新 subagent + 兩段審查（spec 合規→code quality） |
| test-driven-development | 每寫 code task：red→green |
| dispatching-parallel-agents | 省時主軸：建庫/檢索模組/測試並行、三審並行、e2e 多維度並行（上限 3–5） |
| systematic-debugging | 出 bug：根因→重現 test→修，不亂修 |
| requesting-code-review + receiving-code-review | 三審 gate 模板 + 退修回應 |
| using-git-worktrees | 遷移在獨立 worktree，不污染 master |
| verification-before-completion | 每階段：親證（Playwright/Sentry 證據）才算完成（IL-2） |
| finishing-a-development-branch | release：三審過→收束分支 |
| executing-plans | 備選：若不用 subagent-driven 改 inline 批次 |
| writing-skills | **N/A**（本次不產新 skill，不硬湊） |

### D. karpathy 開發紀律（貫穿所有寫 code 環節）

1. **Think before coding**：surface 假設/tradeoff，不靜默選擇。
2. **Simplicity first**：硬切、零供應商抽象、前端零改動——已體現；不加未要求的彈性。
3. **Surgical changes**：每行改動可追溯到此遷移；不順手改鄰近碼；**Gemini 死碼 Phase 3 才清，且只清本遷移製造的 orphan + 明列**。
4. **Goal-driven**：每 task 轉成可驗證 goal（先 fail test → green）。

### E. 並行 vs 序列

- **可並行**：建庫腳本 / `retrieval_openai` 模組 / 各自單元測試；三審；e2e 多維度。
- **必須序列**：Phase 0 建庫 → Phase 1 推薦（驗證檢索品質）→ Phase 2 問答（依賴庫+檢索品質已驗）→ Phase 3 清理。
- 角色：Opus = director / auditor / spec；Sonnet = implementer（TDD red→green / 5x e2e）。
