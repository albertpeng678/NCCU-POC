# NCCU 課程推薦系統 — 交接文件（HANDOFF.md）

> 專案進展史 + WBS + 待辦。給接手的 agent 快速掌握「做到哪、還剩什麼」。
> 最後更新：2026-06-09（**Session 11**：完成 OpenAI 遷移收尾——已 merge master、**完整移除 Gemini**、QA 引用精準度修正、e2e + 三審通過、部署中；見下方 ★Session 11）

---

## ★ Session 11 交接（最新，接手第一個讀）★

> **把 Session 10 的 OpenAI 遷移收尾並上線。**

**已完成（皆已 commit 在 master）：**
1. **修 37 紅燈**（`ba77aca`）：被打斷的 agent 在工作區留半套 `_require_openai_client` 守門，依賴 lifespan，但 bare `TestClient` 不跑 lifespan → 全域 None → 503。改成守門 lazy-init from factory（反映「key 是否設定」而非「lifespan 是否跑過」）。
2. **完整移除 Gemini**（`c2614ef`）：backend runtime 零 `google-genai`（`grep genai backend/` 空）；QA 收斂成單一 OpenAI 串流（刪 replay/stream35）；recommend 刪死 Gemini 函式；**observability Sentry 分類改寫成 OpenAI 版**（`RateLimitError`→rate-limit、`APIError` 429/5xx→high-demand）；刪 11 個 Gemini-only 測試檔。新增 `tests/backend/test_no_gemini.py` 守門防復活。
3. **QA 引用精準度**（`314327f`）：file_search 回 top_k 檢索全集但模型不一定全引用 → 無關課（如查 PM 冒出碳市場）會混進參考課綱。OpenAI annotations 對本庫**實測為空**、內文 `turn0fileN` marker 索引語意**未公開不可靠**（實測 file1≠results[1]）→ 改「課名出現在答案」過濾，fallback 全沒提到時保留全集。
4. **前端課程數**（`1f859c6`）：hero 副標 2877→2718。
5. 測試 388 綠；QA + recommend 真瀏覽器 e2e（Playwright MCP）通過；三審 approve。

**踩過的大坑（已記 memory）：** Playwright MCP 瀏覽器**殘留上次 e2e 的 `page.route` mock**，把 `/qa/stream` 攔下回假 `rate_limited`「伺服器正忙」，但 curl 同端點正常——繞了很久才用唯一 marker grep 後端 log（沒到後端=被 mock）確認，`unrouteAll` 清掉。**OpenAI 帳號其實沒限速**（gpt-5.4-mini 30k RPM/180M TPM）。

**部署狀態：** 已 merge `feat/retrieval-openai`→master。**Railway 環境變數待校正**：確認 `OPENAI_API_KEY`（密鑰，由使用者設）、`OPENAI_VECTOR_STORE_ID=fileSearchStores 換成 `vs_6a26fe2c36b8819182550837ed5fce7d`、`OPENAI_MODEL=gpt-5.4-mini`；移除 `GEMINI_API_KEY`/`FILE_SEARCH_STORE_NAME`/`GEMINI_QA_MODEL`。push 觸發 Railway 自動部署。

---

## ★ Session 10 交接 ★

> **大事：把檢索層整套從 Gemini 硬切到 OpenAI**（使用者決定，因 Gemini 持續不穩）。分支 **`feat/retrieval-openai`**（**未 merge master、未 push**）。spec/plan：`docs/superpowers/{specs,plans}/2026-06-08-retrieval-migration-openai*`。

---

## ★★ 最新狀態（2026-06-09，換機器接手「先讀這段」）★★

> 下面的「✅已完成/⏳還沒做」是 Session 10 早期寫的，**已過時**；以本段為準。分支 `feat/retrieval-openai`，**28 commits ahead of master、未 merge、未 push**。

### 🔴 第一優先：測試紅燈（必先修，否則不能 merge/deploy）
**`pytest tests/ -q` = 37 failed / 378 passed。** 失敗全在 **main.py 層級 API 測試**（`test_api`、`test_stream_api`、`test_qa_post_history`、`test_qa_ephemeral_api`、`test_recommend_budget_hit`、`test_recommend_stream_*`、`test_open_career_api`、`test_qa_stream_*`、`test_qa_condense`、`test_qa_followups`…）。
- **高度懷疑根因＝judge 遷移 commit `cc5db5b`**（把 main.py 的 judge 呼叫改傳 `_openai_client`）引入的 main.py 級破壞——judge subagent 自報「411 passed」，但合到分支後紅。**用 systematic-debugging 先 `git show cc5db5b` 對 main.py 的改動 + 跑單一失敗測試看 traceback**（多半是 import/fixture/`_openai_client` 相關）。先修綠再往下。

### 🟢 這次 session 已完成（已 commit，未 push）
- **新 store 重建（根治名字/系所問題）**：`OPENAI_VECTOR_STORE_ID=vs_6a26fe2c36b8819182550837ed5fce7d`（**取代舊 `vs_6a26b97862...`**）。建庫腳本 `scripts/build_openai_vector_store.py` 改成 **每課注入完整 header（課名+代號+系所+老師，每 2500 字）+ `chunking_strategy` max 4096 / overlap 0**（context7 確認參數）。**根因**：OpenAI 把長課綱（中位 3689 字、53% 課 >3500 字）切多 chunk，中段 chunk 無 header → 模型看不到課名/系所 → 名字不全/捏造/「系所:未顯示於目前片段」。注入後 **每 chunk 都帶完整 header（3 長課實測 11/11 帶名+系所）**；真打「PM 對應課程」系所欄全填、無「未顯示」「課綱未顯示完整課名」。`.env` 已指向新 store（**新機器要自己設 OPENAI_VECTOR_STORE_ID=vs_6a26fe2c...**）。
- **問答品質一連串修法（使用者 live 抓 + 我真打驗）**：① filecite 引用標記串流剝除（狀態機 U+E200/E201，官方 citation-formatting helper）② 台灣繁中（禁「對口」等陸語）③ markdown 表格穩定（system prompt 加 few-shot 範例，`gpt-5.4-mini` 靠規則不夠）④ followup 改短句 10-18 字 ⑤ 答案要分行/粗體/結構 ⑥ 多輪 condense-then-search ⑦ citation 跨掛課**按前 6 碼去重**（`3243c0f`，046008001/011/021 同課多班次）⑧ **grounding 兩層**（`c1a42f2`：Layer1 prompt 照抄課名禁發明、Layer2 `strip_fabricated_courses` 用 retrieved_ids 錨定 + NFKC 正規化模糊比對，取代脆弱全 meta 完全比對）⑨ 清單外 out-of-scope 用 derive 的 `is_legitimate_career`（`6aa4991`，流浪漢/黑道老大→no_match、記者/網紅→照推，真打驗過）。
- **前端 SSE 70cps**（`6f808ff`，使用者已驗）：打字機 20→70cps 配合 OpenAI 逐 token，cache `?v=37`。
- **judge 遷 OpenAI**（`cc5db5b`，⚠️見上方紅燈）。
- **CLAUDE.md/.env.example 文件更新**（`e526990`）。

### ⏳ 還沒做（接手依序）
1. **🔴 修 37 紅燈測試**（見上，先做）。
2. **三審 backend FAIL 的 3 個 blocking**（frontend ✅ / db ✅ 已過）：
   - 🔴 **judge 評到空推薦**：`judge.py build_judge_prompt(result)` 讀 `result["groups"]` 但 pipeline 回**扁平 `result["courses"]`**（含 group 欄）→ judge prompt 永遠空、分數無意義。修：從扁平重建 groups。
   - 🟠 **OpenAI client None 守門**：`_openai_client` 在 OPENAI_API_KEY 缺時 None → handler AttributeError 而非乾淨 503。加守門 + lifespan 警告。
   - 🟠 **Gemini env 硬必需**：見下方 #3。
3. **★ 完全移除 Gemini（使用者明確要求「與 Gemini 毫無關係」）**＝原 Phase 3 清理，且**取代**上面三審 #3：移除問答的 `replay`/`stream35` Gemini 模式（只留 OpenAI `stream`）、Gemini `_client`、`GEMINI_API_KEY`/`FILE_SEARCH_STORE_NAME` 讀取、`qa.py` 的 `answer_question`/`answer_question_structured`/`stream_answer_structured`/`_visible_text_from_chunk`/Gemini 模板/雙 SDK 相容碼、`recommend.py` Gemini 死碼（`stage1_retrieve`/`stage2_group`/同步 derive 等）、`requirements.txt` 的 `google-genai`、`.env.example` Gemini 鍵。移除後 `import backend.main` 無 google-genai 也要起得來。**這會大量動 main.py/qa.py/recommend.py + 刪 Gemini 模式測試**——小步、常跑測試。
4. **Playwright 5x e2e gate**（推薦+問答全流程）+ 截 PNG 給使用者「對」（Live demo gate；本 session 多為真打 curl + 使用者瀏覽器手測，未正式 5x）。⚠️ **本機 e2e 網路坑**：頁面開 `127.0.0.1:8000`，前端 `CONFIG.API_URL` 對 localhost 寫死會走 IPv6 `::1` 連不到（後端綁 127.0.0.1）→ 解法用 Playwright `addInitScript(()=>window.__API_URL__="http://127.0.0.1:8000")` 注入（前端為 e2e 設計的覆蓋點）。
5. **career_budget**：目前 100/100 是用**舊 store** 跑的；course_id 不變故仍可用，但若要與新 store 一致，可 `ONLY_MISSING=1` 重算（非必須）。
6. **部署**：Railway 設 `OPENAI_API_KEY`/`OPENAI_VECTOR_STORE_ID=vs_6a26fe2c36b8819182550837ed5fce7d`/`OPENAI_MODEL=gpt-5.4-mini`（**移除 Gemini env**）→ merge feat/retrieval-openai → git push → `curl https://nccu-course.up.railway.app/health` 確認 `retrieval_backend=openai` → Sentry MCP 監看。
7. **清理**：刪除本 session 建的 probe 暫存 store（`vs_6a26b9.../6a26fb.../6a26fc.../6a26fe0d...` 等小庫）+ 舊正式 store `vs_6a26b97862...`（切新 store 後）。`example.com` 雜檔（docs agent 報已不存在）。

### 換機器 `.env` 完整設定（一五一十；`.env` gitignored、不跨機，要自己建）
```ini
# === 必填 ===
OPENAI_API_KEY=sk-...                 # ⚠️ 你自己的 OpenAI key（platform.openai.com/api-keys）。密鑰，勿提交/勿貼進任何會 push 的檔。
OPENAI_VECTOR_STORE_ID=vs_6a26fe2c36b8819182550837ed5fce7d   # 新 store（含 header 注入根治名字/系所）；非密，可記。
OPENAI_MODEL=gpt-5.4-mini             # 推薦標註/問答/judge/derive 一律此模型
ALLOWED_ORIGIN=*                      # 本機測試用 *（部署填 frontend URL）
# === 選填 ===
SENTRY_DSN=                           # 本機「留空」（別污染正式 Sentry）；正式值是公開的，見 frontend/app.js 的 CONFIG.SENTRY_DSN
# === 不要寫進 .env（用環境變數臨時傳）===
# DATABASE_URL：本機跑要連 Railway Postgres 時，用 `railway variables -s Postgres --json` 撈 DATABASE_PUBLIC_URL，
#   以環境變數傳給指令（DATABASE_URL=... python ...），勿寫進 .env（會覆蓋部署設定；且 auto-mode 會擋把 DB 密鑰落地檔案）。
#   問答多輪/career_budget 才需要 DB；純測檢索/推薦 live 不需要。
```
> 換 OpenAI key＝看不到舊 Vector Store（store 綁帳號）。新機器用**同一把 key** 才能存取 `vs_6a26fe2c...`。

### 本機跑法（新 store）
```bash
# .env 設好上面後：
KEY=$(grep '^OPENAI_API_KEY=' .env | cut -d= -f2-)
SENTRY_DSN='' OPENAI_API_KEY="$KEY" OPENAI_VECTOR_STORE_ID=vs_6a26fe2c36b8819182550837ed5fce7d OPENAI_MODEL=gpt-5.4-mini ALLOWED_ORIGIN='*' \
  nohup .venv/bin/python -m uvicorn backend.main:app --port 8000 --host 127.0.0.1 &
# 前端同源開 http://127.0.0.1:8000/（瀏覽器硬重整載 ?v=37）
# 問答多輪要 DB：再前綴 DATABASE_URL="$(railway variables -s Postgres --json | python -c 'import sys,json;print(json.load(sys.stdin)["DATABASE_PUBLIC_URL"])')"
```
⚠️ **.env 編輯**：曾因 API_KEY 行無換行尾、`echo >> .env` 黏成同一行污染 key → 用 python 改 .env；DB 密鑰勿落地檔案（auto-mode 會擋）。

---

> ↓↓ 以下為 Session 10 早期版本（已過時，僅留參考）↓↓

### 定案決策（不可再迴避）
- **供應商一律 OpenAI**：檢索=**Vector Stores**、生成=**`gpt-5.4-mini`**（已真打確認此 model id 有效）、含 career_budget + judge。Gemini 設計決策 #12「固定 2.5-flash」**已被取代**。硬切、不留 Gemini 回退抽象（安全網=上線前 e2e gate + 三審）。
- **OpenAI Vector Store**：`OPENAI_VECTOR_STORE_ID=vs_6a26b97862ec8191b9bfa34727a0c8fb`（**2718 課全灌、檔名=course_id、attributes 帶 course_id/syllabus_url**）。建庫腳本 `scripts/build_openai_vector_store.py`（ONLY_MISSING 可續）。
- **citation 用 path B**（已 context7+真打確認）：file_search 的 **annotations 只在模型內文主動引用才有（常空）**；要可靠拿「答案根據哪幾門課」用 **`include=["file_search_call.results"]`**（= Gemini grounding_chunks 對等）。`retrieval_openai.course_ids_from_search_results`。
- **OpenAI SSE 逐 token 平滑**（一答約 459 delta，非 Gemini 爆發式）→ **前端打字機緩衝(20cps)要拔/改**（尚未做，見待辦）。
- **followup 兩段式**：串流乾淨 markdown 答案（第一段，file_search）+ 答案後一個 **structured-output 小呼叫**（`responses.parse` text_format Pydantic、無 file_search）生 3 個 followup。**不可把 answer 包進 JSON 串流**（會重蹈 Session 5「JSON 鷹架洩漏」覆轍）。`qa.generate_followups`。

### ✅ 已完成（程式+審查+live 親證）
- **Phase 0 建庫**：`backend/openai_client.py`(AsyncOpenAI singleton, startup 建避免 event-loop 污染)、`backend/retrieval_openai.py`(`search_skill`/`course_ids_from_annotations`/`course_ids_from_search_results`)、建庫腳本。**2718 課灌完、7 題繁中 smoke 全準**。
- **Phase 1 推薦**（`backend/recommend.py`/`main.py`）：fan-out 改 `search_skill`、stage2/derive 改 `_openai_structured`(`responses.parse`)、`/health` 加 `retrieval_backend`/`model`。spec審+質審+**3 輪修**（含抓到「**合併池塌成 1 門**」regression：course_name 設空→`deduplicate_by_name` 全併；修法=從 courses_meta 填真課名）。**live 親證 PM 30 門池/17s**。**career_budget 100/100 用 OpenAI 重算**（健康池 23-29）。
- **Phase 2 問答**（`backend/qa.py`/`main.py`）：`stream_answer` 改 Responses+file_search、path B citation、out-of-scope(results 空→查無)、followup 兩段式。spec審 **SPEC COMPLIANT**。**live 親證 /qa/stream：citations 5、followup 3、乾淨 markdown 逐 token**。

### ⏳ 還沒做（接手繼續）
1. **Phase 2 code quality 審查**（qa.py 重寫，spec 審過、質審待做）。
2. **前端 SSE 平滑化**：OpenAI 逐 token 已平滑，前端 `createTypewriter`(20cps 緩衝)+漸進 markdown 要重評估（拔緩衝/直餵 token），否則雙重緩衝拖慢。**前端零改動原則的唯一例外**。
3. **Playwright 5x e2e + 截 PNG 給使用者「對」**（Live demo gate）：推薦（清單內/外、卡片/分組/換一批）+ 問答（多輪「那金融呢」、離題查無、citation 對回真課綱、followup chips）。先徹底讀 playwright-skill（已讀 pitfalls+斷言）。
4. **三審 release gate**：frontend/backend/db auditor 並行、全過才 release。
5. **Phase 3 清理**：移除 Gemini 死碼（`answer_question`、`_visible_text_from_chunk` 若 stream35 也廢、雙 SDK 相容碼）、`google-genai` 依賴、舊 env；更新 CLAUDE.md（技術棧 Gemini→OpenAI、設計決策）。
6. **部署**：Railway 設 `OPENAI_API_KEY`/`OPENAI_VECTOR_STORE_ID`/`OPENAI_MODEL` → merge → git push → Sentry MCP 監看。

### 本機跑法（OpenAI 後端）
```
# .env 已有 OPENAI_API_KEY / OPENAI_MODEL=gpt-5.4-mini / OPENAI_VECTOR_STORE_ID=vs_6a26b97862ec8191b9bfa34727a0c8fb
SENTRY_DSN='' OPENAI_API_KEY=<.env> OPENAI_VECTOR_STORE_ID=<.env> OPENAI_MODEL=gpt-5.4-mini ALLOWED_ORIGIN='*' \
  .venv/bin/python -m uvicorn backend.main:app --port 8000 --host 127.0.0.1
# 前端同源：開 http://127.0.0.1:8000/
# career_budget 重算：上面 env + DATABASE_URL=<Railway DATABASE_PUBLIC_URL，用 railway variables -s Postgres 撈> CONCURRENCY=2 python scripts/build_career_budget.py
```
⚠️ **.env 編輯小心**：曾因 API_KEY 行無換行尾、`echo >> .env` 黏成同一行污染 key（已修）。用 python 改 .env、勿把 DB 密鑰寫進檔案。

---

## ★ Session 9 交接（最新，換機器接手第一個讀）★

> **換機器先讀這段。** 兩條主線：(A) production 緊急回退；(B) Track C 已完成但未上線（卡 3.5）。

### A. 🔴 production 緊急處置：問答自 3.5 回退 2.5（已 push master）

- **動作**：`backend/main.py` `_QA_MODE` 預設 `stream35`→**`stream`**（2.5 串流）。**已 commit + push `origin/master` → Railway 重新部署。**
- **為什麼**：實測 2026-06-08 **`gemini-3.5-flash` 持續 503 "high demand"、`gemini-2.5-flash` 正常**。問答 production 跑 3.5，雖有 SDK full-jitter 重試（429/503×5）能最終成功，但**退避階梯 1→2→4→8→16s 會讓問答回應時間爆長**——問答時間敏感，這是錯的取捨。
- **web 研究結論**（為何 Google API 這麼不穩）：**不是壞掉、不是我們的錯**，是「新模型 GA 上線潮 + dynamic shared quota」的**可預期現象**：3.5 才 GA ~3 週（2026-05-19），全球搶用、容量還沒擴到位 → 用 503 背壓擋流量，**約 1-3 週緩解**。`status.cloud.google.com` 不會顯示（容量節流不算事故）；**付費/Vertex 救不了 503**（只解 429），唯一真解是 provisioned throughput（企業級、PoC 不值得）或等待/降級。2.5 沒事是因為容量已擴一年。
- **驗證（已確認）**：`/health` 已外露 `qa_mode`（commit `c50e102`）→ `curl https://nccu-course.up.railway.app/health` 回 `{"status":"ok","qa_mode":"stream"}` 即線上跑 2.5。**已實測線上 = `stream`（2.5）。** 以後查線上模式都用這個，**不要在 Railway 釘 `QA_MODE` 環境變數**（會變隱形覆蓋的技術債、與程式碼真相來源打架——使用者明確要求避免）。Railway 目前**無** QA_MODE/REC_ 等 env 覆蓋（已用 `railway variables` 確認），程式碼預設即真相。

### B. ⚠️ 戰略決策（使用者拍板，不可再迴避）：必須正面治本 2.5 的 JSON 問題

> 「**不能用『切去 3.5』當作迴避 2.5 JSON 脆弱性的手段。**」3.5 對時間敏感的問答不可靠（503），我們被逼回 2.5，所以 **2.5 的根本問題必須正面修，不能再繞過**。

- **問題本體**（CLAUDE #4/#15）：2.5 + file_search **不能配 `response_mime_type=json`/`response_schema`** → 只能「從散文裡解析 JSON」→ 偶爾**漏 JSON 鷹架 / citation 漏 / 答案被 grounding 複寫 / 偶爾整段非 JSON**。Session 5 的三層強化（`parse_qa_response`+`json_repair`+`_strip_fences`、`_partial_answer` 守門）是**緩解非根治**。
- **待辦（高優先，新機器接手主線）**：用 systematic-debugging + 真實 e2e，把 2.5 問答的 JSON/grounding 可靠性**做到根治級**（而非靠 3.5 繞過）。可能方向：更強的串流 JSON 守門狀態機、或「2.5 用 file_search 拿 grounding + 另一次無 file_search 呼叫做結構化」的兩段法、或重新評估 prompt 結構（但 #15：動 system_instruction/拿掉 JSON 包裝會破 grounding，須重驗）。**先 brainstorming spec 再動工。**

### C. Track C（推薦遷 3.5 + fan-out 合併省成本）— 已完成，未 merge，卡 3.5 503

- **分支 `feat/recommend-3.5`**（6 code commits + docs，**309 後端測試綠**，未 merge/push）。內容：
  1. `REC_MODEL` env（預設 3.5、可回退 2.5）+ model-aware `_thinking(kind)` helper（thinking_budget→thinking_level）。
  2. **`consolidated_retrieve_async`**：fan-out 6-8 支 file_search **合併為單一支帶全部技能**（`CONSOLIDATED_TOP_K=32`、「只檢索一次」）→ **每請求併發/成本砍 ~6-8x（NT$5-7→~NT$1）**，也根治我們自製的 503 突發。`REC_RETRIEVE=consolidated`(預設)/`fanout`(回退) 切換。
  3. **`generate_with_fallback`**：推薦 async 熱路徑持久 503 → **自動降級 3.5→2.5**（同步換 thinking_config）。
  4. **空池安全網**：consolidated 回空池（2.5 無結構化保證、偶回散文）→ 自動補打 fanout（消爆炸半徑）。
- **卡點**：完整 judge A/B（3.5 vs 2.5、consolidated vs fanout）+ consolidated 3.5 路徑上 `response_schema` 治本 + Playwright，**全卡 3.5 503**。實跑過 2.5+consolidated：撈得到課但單支 JSON 非確定性曾空池（已加安全網）。
- **收尾待辦（等 3.5 退燒）**：跑 A/B → 過則翻預設、不過則 Railway 設 `REC_RETRIEVE=fanout`/`REC_MODEL=2.5` → merge master → `railway ssh` 容器內 `ONLY_MISSING=1 python scripts/build_career_budget.py` 灌新 50 budget → 刪分支。spec/plan：`docs/superpowers/{specs,plans}/2026-06-08-recommend-gemini-3.5-migration*`。

### 503 韌性研究的可複用結論（context7 + web，3 份平行 agent）
- 既有 SDK `HttpRetryOptions`（main.py：full-jitter 指數退避、429/503×5）**已是主流做法**，不需另做手動退避（會雙重重試、放大 RPM）。
- 真正缺口是**降併發**（Track C consolidated 已解）與**降級**（Track C fallback 已解、問答暫用回退 2.5 替代）。
- **不要**用 request hedging（對過載伺服器加倍打 = 更糟）。fanout semaphore / 尊重 429 retry_delay / 提高退避 cap = 暫緩，先合併後觀察。

---

## ★ Session 8 交接（最新，換機器接手第一個讀）★

> 分支 **`master`**，**已 push `origin/master`（commit `d6b0490`，Railway 自動 build 部署）**。
> 後端測試 **256 passing**；前端資源版本 **`?v=34`**。

### 🟢 已修：先前高優先 bug「Event loop is closed」（Session 8，commit `d6b0490`）

**原症狀**：清單外職涯走 `/recommend/stream` 時，只要該後端程序此前跑過任何一次 `POST /recommend` 即時路徑，之後所有 `/recommend/stream`（清單外）與 `/qa/stream` 都秒噴 `Event loop is closed`。

**根因**：POST /recommend 曾用 `await asyncio.to_thread(build_recommendation_instrumented, ...)`（`21e6a93`），而同步 `build_recommendation_instrumented` 內部 `asyncio.run(_run())` 在 worker thread 開**用完即關的 loop L2**，`_run` 又 `await` 模組層共用 `_client.aio` → 把共用 httpx async client 綁到 L2 → L2 關閉後主 loop L1 上任何 `_client.aio` 呼叫（stream）都炸。**= 把單請求崩潰換成污染整個 worker 程序的更廣 regression**；單元測試/code-review 沒抓到（沒測「同程序下一個 async 請求」），e2e 才抓到。

**修法（`d6b0490`）**：
- 抽出 async `build_recommendation_instrumented_async`（直接 `await fanout_retrieve_async / stage2_annotate_pool_async`），POST /recommend handler 直接 `await`（全程主 loop L1，不開新 loop、不 to_thread）。
- 清單外 derive 也改 `await derive_skills_for_career_async`（review 發現的既有阻塞，順手修）。
- 同步 `build_recommendation_instrumented`（仍 `asyncio.run`）**只留給 sync 呼叫端**（`build_recommendation` / sync 測試 / scripts）——伺服器路徑勿用。
- 回歸測試 `tests/backend/test_recommend_loop_fix.py`：鎖定「pipeline 兩階段都在呼叫端 loop」+ 空候選 ValueError。

**驗證（已過）**：256 後端測試綠；嚴格 code review（正確性 reviewer 判「根因已根除」、品質 reviewer「ship it」）；Playwright 全端 e2e —— 瀏覽器內先打 polluter `POST /recommend`(200) → 再由真 UI 點職涯觸發 victim `/recommend/stream` → 渲染 13 卡、**零「Event loop is closed」**；清單外「記者」live 走 derive async 正常；伺服器端 log 零 loop 錯誤。

### ✅ 本 session 已完成並 commit（未 push）

1. **離線預算 career_budget（核心提速，秒回）**（`ebb6671`/`68299b0`/`9f18962`）：
   - 50 固定職涯離線跑完整 pipeline、整池存 Postgres `career_budget` 表（`backend/schema.sql` 已加表）；線上 `get_budget` 命中即**秒出（0 即時 AI）**，未命中落回即時路徑。
   - `backend/career_budget.py`（serialize/deserialize/get_budget/upsert_budget；deserialize 補 group/reason 預設、跳壞課防漂移）；`backend/recommend.py` 加 `stream_recommendation_from_budget`（命中時 SSE 5 階段瞬間 + result）；`main.py` POST 與 stream 都先查 budget。
   - **離線腳本 `scripts/build_career_budget.py`**（force-add，gitignored）：`CONCURRENCY`/`ONLY`/`ONLY_MISSING` env；**已對 Railway 正式 DB 灌好 50/50 職涯**。實測命中：PM 1.24s、金融科技 1.29s、律師 0.72s。
   - ⚠️ code-review 抓到的 blocking：串流命中預算原本仍背景叫 judge（燒錢）→ `9f18962` 用 `not budget` gate 修掉。
2. **柴犬等候動畫**（`1e8180c`/`0800c19`/`284c3c4`）：取代舊步驟條。recolored Lottie（`frontend/shiba.json`，亮度分桶重上**嚴格三色**）+ done-driven 數字（`easeApproach` Weibull τ=55s 逼近但不超過總數，後端 done 才 `fillToDone`）+ 柴犬旁白。純函式核心 `frontend/shiba-progress.js`（node:test）。**硬規矩**見 memory `waiting-mascot-design-constraints`：三色含吉祥物、禁 emoji、累積不倒數、不要積木感、領巾/外框 navy 不要 cyan（曾被嫌「藍光邊像阿飄」）。
3. **Q&A 失敗復原**（`abbb84e`/`3512f93`/`bbcd620`）：後端 `qa.py` `classify_qa_error`（429/503→rate_limited、timeout→timeout）+ `finalize_qa_answer` 回 4-tuple 帶 `no_match`；SSE error event 帶 `error_type`、done 帶 no_match。前端 `qa-recovery.js`：過載→**忙線重試泡泡**（點一下重問）、查無資料→引導；差異化、三色無 emoji。POST fallback 失敗也走重試泡泡。
4. **多輪追問修復**（`819e06c`/`aaffa74`）：POST /qa 兩模式統一 **history-based**（根除 `Invalid previous_interaction_id`）；`_SYSTEM_INSTRUCTION` 加「# 多輪對話脈絡」段 → 「那金融呢」這種簡短追問會結合上文意圖再檢索。live 3/3 + production e2e 親證。
5. **Sentry 降噪**（`e9ab554`/`3509eb9`）：後端 `observability.py` `classify_and_downgrade`(before_send) 把暫時性 Gemini 429/503/spending-cap 降 warning + 歸群、真 bug 維持 error；`stream_traces_sampler`。前端 **localhost 不啟用 Sentry**（杜絕本機 e2e/開發污染正式專案——我之前不慎觸發過真實錯誤回報，根因就是前端 DSN hardcode 且在 localhost 也送）。
6. **compose 關 thinking 提速**（`c2e48e9`）：`stage2_annotate_pool_async` 設 `thinking_budget=0`，A/B 實測（`scripts/probe_compose_thinking_ab.py`）此 schema 約束的「分類+排序+短理由」任務關 thinking **快 ~3x（33s→11s）judge 品質不掉**。見 CLAUDE.md 設計決策 #10 更新。**Q&A 串流的 thinking 仍不可關**。
7. **推薦分頁 end-state**（`899de0e`/`de35052`）：翻到底顯示「看完了」end-state；換職涯走目錄直接推薦。**殘留 end-state bug** 修法：清理移到 `showLoadingShell`（覆蓋成功/錯誤/no_match 所有路徑）。**5x 連續切換職涯 e2e 親證 staleEndStates:0、0 flake**。
8. **POST /recommend asyncio 崩潰**（`21e6a93`）：原崩潰（asyncio.run in running loop）已修，但該手段引入「Event loop is closed」regression → **Session 8 `d6b0490` 已正解重修，見上方 🟢。**
9. 其他：手機 rubber-band 防彈跳 + Q&A 隱藏 hamburger（`e29ac15`）；清單外 notice 框移除 cyan 螢光邊（`9f58b88`）。

### 驗證狀態（誠實標註，勿過度宣稱）
- ✅ 殘留 end-state：真後端 Playwright **5x 連續**切換職涯，全 `staleEndStates:0`、職涯名正確、換一批鈕回來。
- ✅ 多輪追問：live 3/3 + production e2e。
- ✅ career_budget：Railway 正式 DB 50/50 灌好，命中秒回（PM 1.24s 等）實測。
- ✅ **清單外職涯完整 e2e（Session 8）**：「記者」live POST /recommend 走 derive async → 回「紀實採寫」等相關課（42s, 200）；polluter→victim stream 序列無「Event loop is closed」。
- 256 後端測試全綠（Session 8 新增 `test_recommend_loop_fix` 補上「pipeline 在呼叫端 loop」的回歸覆蓋；跨請求 async client 污染另由 e2e 把關）。

### 換機器須知（沿用，無變動）
- `.env` gitignored：`GEMINI_API_KEY`（同一把，換 key 看不到 store）、`FILE_SEARCH_STORE_NAME=fileSearchStores/nccucourses1142-znuka50qq2y2`、`SENTRY_DSN`（見 app.js 同一個公開值）、`ALLOWED_ORIGIN=*`。
- 本機 backend：`SENTRY_DSN='' ALLOWED_ORIGIN=* DATABASE_URL="<Railway DATABASE_PUBLIC_URL>" python -m uvicorn backend.main:app --port 8000 --host 127.0.0.1`。**本機務必 `SENTRY_DSN=''`**（別污染正式 Sentry）。前端同源由 backend StaticFiles 服務，開 `http://127.0.0.1:8000/`。
- career_budget 已在正式 DB；本機連同一個 Railway DB 即可命中。離線重算：`ONLY_MISSING=1 CONCURRENCY=2 GEMINI_API_KEY=.. FILE_SEARCH_STORE_NAME=.. DATABASE_URL=.. python scripts/build_career_budget.py`。

---

## ★ Session 5 交接（最新，換機器接手必讀）★

**做了什麼**：解決 Q&A 三個體感問題 ——「串流會漏 JSON 鷹架 / Gemini 一坨一坨吐 chunk 不平滑 / 表格以 raw markdown 顯示」。

**關鍵決策**：探索過 `gemini-3.5-flash`（掛 file_search 時能原生 `response_schema`，2.5 不行會 400 → 從源頭免「自由文字解析 JSON」）。但 **3.5 現階段 high-demand 503 嚴重、延遲變異大（13~39s）**，production 跑 2.5 才穩 → **決定 Q&A 留 2.5 串流**，用程式手段壓問題。3.5 成果保留：`answer_question_structured`（replay 模式）已 commit；3.5 *真串流* 實驗在 `git stash`。

**怎麼解（已實作於 `feat/qa-hardening-smooth-stream`，未上線）**：
- **(A) JSON 三層強化**：`parse_qa_response` → `json.loads`→`json_repair`→`_strip_fences`（剝殼後仍以 `{` 開頭就 blank，**絕不漏鷹架**）；`stream_answer` 加 **JSON-first 守門**（模型若沒寫 prose 直接吐 JSON，用 `_partial_answer`(json_repair 增量)只串乾淨 answer 值）。
- **(B) 平滑串流打字機**：SSE `token` 改餵既有 `createTypewriter`（定速緩衝，解耦 Gemini 不均 chunk）；`drainCount` backlog 自適應；打字 **20 cps**（使用者偏好較慢）。**表格逐列滑順長出**（借鑑參考 bot、Playwright 動態實測其逐列建構）：`safeMarkdownPrefix` 改「保留已完成列、只藏正在打字的半截列；湊齊表頭+分隔線才現」→ 不再整塊 snap-in、不露 raw `|`。
  - **關鍵修正**：`renderFinal` 用 `mdToHtml(data.answer)`（完整權威答案**不套** streaming heal）—— 否則「以表格列結尾」的答案會被 heal 誤砍最後一列、看似沒答完。committed 區塊同理用 `mdToHtml` 不套 heal（見 doRender 註解的不變式）。
- **(C) `QA_MODE`**：預設 `stream`(2.5 強化)；`replay`(3.5 非串流，慢但結構上不漏)為 env 一鍵回退選項。

**驗證**：後端 206 + 前端 20 測試綠；**Playwright MCP 真後端動態實測通過**（串流中+最終皆無 `{"answer"`/```json；**表格 1→2→3→4 列逐列長出、rawPipe 全程 false**；citations/followups、0 console error）；code review + /simplify 皆過。

**429 釐清**：本機開發時撞的 embedding 429 是**爆量測試**造成（每提問都 embed 問句 + SDK 重試放大），**production 正常**；使用者是 Tier 1。非阻礙。

**待辦/未做**：① ~~上線~~（已 push master 觸發 Railway 部署；前端 cache-bust `?v=19`）。② `/recommend` 是否遷 3.5（另議，先 probe 延遲/品質）。③ 清理：刪 `createProgressiveRenderer`/`stripInProgressTable` 死碼、收斂單一渲染器、把 `safeMarkdownPrefix` 抽到 progressive-md.js 單測。④ Layer 4「sentinel 換格式」徹底根治（需重驗 grounding #15）。⑤ ~~git committer email~~（本 repo 已設 albertpeng678@gmail.com；既有 commit 仍掛公司信箱，未重寫歷史）。

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
6. **部署修復（同源）**：`$PORT` 未展開(Dockerfile shell form) + `init_pool` 韌性(連不到 DB 不崩) + **backend 同源服務前端**(FastAPI StaticFiles，因 root `railway.toml` 跨服務污染、第二個 nginx service 會誤 build 後端)。**單一網址 https://nccu-course.up.railway.app**（使用者後改；舊 `nccu-poc-production…` 已失效），git push 觸發。⚠️ 前端靜態檔務必整包 `COPY frontend/ ./frontend/`——Session 8 曾因 Dockerfile 寫死列舉漏掉新增檔 → 線上 404 → app.js ESM import 失敗全站死（`tests/backend/test_frontend_assets_shipped.py` 已守門）。
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
