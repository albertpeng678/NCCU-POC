# 推薦延遲根治：並行 fan-out 檢索 + 整池預標註 + 前端分頁換一批

> 設計文件（brainstorming 產出）。日期 2026-06-04。
> 目標：把職涯推薦首次載入從 ~150s 降到 ~70s、「換一批」從 ~25s 降到瞬間，並順帶根治間歇空結果與常態 429。

## 1. 問題與 Root Cause（全程量測佐證）

**症狀**：`/recommend`（與 `/recommend/stream`）常態耗時 ~150–180s；間歇出現「Stage 1 returned no candidate courses」；偶發 429。使用者直覺「幾 MB 資料的 RAG 不該這麼慢，是程式端問題」——經驗證**完全正確**。

**Root cause（單一）**：stage1 用 gemini-2.5-flash 的 **agentic file_search 工具迴圈**做檢索。要取得 20+ 門候選，模型必須「邊想邊反覆檢索」，thinking 吐出大量 token 並逐一解碼。三個症狀同源：

- **慢** → thinking 解碼時間（非資料量、非輸出量、非網路）。
- **間歇空結果** → 想/搜過頭，最後 JSON 陣列沒輸出 → `extract_json_array` 回 `[]`。
- **429** → 慢呼叫貼著 180s timeout → SDK 重試整條 → 重打 embedding → 燒 per-project RPM。

**量測證據**（隔離實驗，職涯=資料科學家）：

| 設定 | 耗時 | 候選數 | thinking_tok | retrieved_tok |
|---|---|---|---|---|
| 現況（auto thinking，無 top_k） | 124.8s | 24 | 18,924 | 70,573 |
| thinking_budget=0 | 7.2s | 4 | 0 | 235 |
| thinking_budget=2048 | 103.0s | **0**（空吐） | 14,224（超過預算） | 49,098 |
| top_k=15（auto thinking） | 31.3s | 9 | 4,946 | 463 |
| top_k=30（auto thinking） | 77.7s | 24 | 11,258 | 18,311 |

**排除的假設**：多輪 agentic 檢索（`grounding nqueries=0`）；`thinking_budget`（軟目標，設 2048 仍燒 14k，控不住）；`top_k` 單獨（需 30 才夠課、仍 77s）。**結論：要在「單次大檢索」拿到 20+ 課，本質上就是 ~77–124s；必須改架構。**

## 2. 設計總覽

把 stage1「一次大檢索」拆成「**每技能一支小而快的平行檢索**」，合併成候選池；stage2「**一次標註整池**」（排序+分組+理由）；整池回傳前端，前端**分頁**呈現，「換一批」純前端切片。

```
career + skills
   │
   ├─[stage1 fan-out]─ 6 技能 × file_search(top_k=8, 各要 ~5 課) 並行 asyncio.gather
   │                    → 合併去重 → 候選池 ~24–30 課                （實測 ~42s）
   │
   ├─[stage2 annotate-pool]─ 對整池排序+分組(core/supporting/extended)+生成理由
   │                          → join metadata → 完整 ranked 池        （實測 ~25–35s）
   │
   └─ 回傳整池（已分組、已標註、已附課綱連結）
        → 前端顯示第 1 批（前 10）
        → 「換一批」前端切下一批 10（0s，免連線）
        → 池抽乾 → 前端以新 seed 再呼叫 /recommend 續池
```

**淨效果**：首載 ~150s→**~70s**；換一批 ~25s→**0s**；間歇空結果**根治**（多點檢索無單點失敗，任一技能空仍有其他補足）；429 **降低**（短呼叫快速釋放 RPM、不貼 timeout 重試）。**生成模型全程維持 gemini-2.5-flash**（使用者要求）。

## 3. 元件設計

### 3.1 stage1：並行 per-skill fan-out（`backend/recommend.py`）

新函式 `fanout_retrieve_async(client, store_name, career, skills) -> list[dict]`：

- 對 `skills` 每項建一支查詢：prompt 聚焦單一技能、要求 ~5 門課、`FileSearch(top_k=8)`。
- `asyncio.gather` 並行；單支失敗（503/空）→ 回 `[]`，不拖垮整體。
- 合併所有結果 → `correct_candidate_ids`（沿用，修正抄錯 id）→ `deduplicate_by_name` 去重 → 回候選池。
- 池大小由 `FANOUT_PER_SKILL`（每技能要幾課）與技能數決定；目標 24–30。

**取代**：`stage1_retrieve_async`（舊單次大檢索）。同步版 `stage1_retrieve` 與 POST 路徑一併改用 fan-out 的同步包裝或保留供測試。

設計參數（`recommend.py` 常數）：
- `FANOUT_TOP_K = 8`：每支 file_search 檢索的 chunk 上限（壓制 retrieved 量→壓 thinking）。
- `FANOUT_PER_SKILL = 5`：每技能要求的課數。
- `POOL_TARGET = 30`：池上限（合併後若超過取前 N，依各技能輪流交錯以保多樣）。

### 3.2 stage2：一次標註整池（`backend/recommend.py`）

改 `stage2_group_async`：prompt 從「選 10 門分三組」改為「**為池中每一門**課標 `group`（core/supporting/extended）+ 生成理由 + **依推薦強度全域排序**」。`response_schema` 改為**扁平 ranked 清單** `[{course_id, group, reason_lead, reason_points}]`（依 rank 由高到低），取代原巢狀 `groups`。lead + 2 個粗體 term/detail 結構保留。

- 量測：標註 20 課=21s（與 10 課同）、30 課=34.5s → 整池標註成本可接受。
- course_id 復原 + 去重邏輯（原 `build_groups` 內）抽成可作用於扁平清單的版本：逐課 join metadata、前綴復原、跨課依課名去重，保留 rank 與 group 標籤。

### 3.3 回應形狀（`backend/models.py`）

`RecommendResponse` 改為回傳**扁平 ranked 課程清單**：
- `courses: list[Course]`，每門含 `course_id, name, department, teacher, credits, group, reason{lead,points}, syllabus_url`，依 rank 排序。
- 新增 `batch_size: int = 10`（前端每批顯示數，後端給預設）。
- 前端依 rank 切批、再按 `group` 歸區塊呈現；後端不分頁。
- 既有前端「core/supporting/extended 三區塊」渲染改為「依當前批次的 `group` 欄位分流」。

> **決策：分頁在前端做**（client-side pagination）。首載回傳整池（~30 課 JSON，payload 微小），前端顯示前 10、其餘留存，「換一批」純前端切片 → 真・0 秒、免 server 狀態、免新端點。

**批次語意（「每批 10 門」× 三組如何對應）**：stage2 為每門課標註 `group`（core/supporting/extended）與**全域 rank**（推薦強度排序）。整池即「依 rank 排好、各帶 group 標籤」的清單。前端「一批 = 依 rank 取下一個 10 門」，呈現時再依各自 `group` 歸到 core/supporting/extended 三個區塊顯示。因此每批都是「新的 10 門、仍分三組」。第 1 批=rank 1–10、換一批=11–20、再換=21–30。

### 3.4 串流端點（`backend/recommend.py` `stream_recommendation` + `main.py`）

`_STAGES` 對應更新：retrieve 階段內部是並行 fan-out（可在各技能完成時 yield 進度，選配）；compose 階段為整池標註。最終 `result` 事件帶整池。空池/no_match 行為沿用既有（清單外→引導問答；清單內空→error）。

### 3.5 前端（`frontend/app.js` + `index.html` + `style.css`）

- 接收整池 → 存於前端狀態（如 `state.pool`、`state.shown`）。
- 顯示前 `batch_size` 門（依 group 結構呈現）。
- 「換一批」：從 `pool` 取下一批未顯示的 10 門，重繪；**不發網路請求**。
- 池抽乾（剩餘 < batch_size）：以新 `seed` 呼叫 `/recommend` 續池，append 進 `pool`；UI 顯示載入態（沿用 streaming stepper）。
- 維持既有 navy 指揮台風格與 SVG reroll icon。

## 4. 錯誤處理

- **單技能檢索失敗（503/空/逾時）**：該支回 `[]`，其他技能補足；只有**全部**技能皆空才視為 no_match/error。
- **整池標註 503**：small retry（沿用 SDK retry_options）；最終失敗→error 事件，前端顯示「稍後再試」。
- **池小於 batch_size**：直接顯示現有數量，不報錯；「換一批」觸發續池或顯示「已涵蓋主要推薦」。
- **429**：短呼叫降低觸發；保留現有 timeout/retry 設定為兜底。

## 5. 測試（TDD）

純函式（不打 API）優先：
- `fanout_retrieve_async`：mock 各技能回傳 → 驗證合併、去重、單支失敗容錯、池上限交錯。
- `stage2 annotate-pool`：mock schema 輸出 → 驗證整池都被標註、排序保留、`build_groups` 去重。
- 前端分頁邏輯：給定 30 課池 → 第 1 批 10、換一批給 11–20、再換給 21–30、再換觸發續池。
- 既有 `deduplicate_by_name` / `correct_candidate_ids` / `build_groups` 測試沿用。

整合驗證（Playwright，少量、避免燒 RPM）：資料科學家 + 產品經理(PM) 各一次 E2E，確認首載 < ~80s、換一批瞬間、無空結果。

## 6. Out of Scope（後續）

- **50 職涯離線預算快取**（HANDOFF #11 原 Option B）：可作為 phase 2，把清單內職涯的池預先算好存起來，首載趨近只剩 stage2 或 0s。本 spec 先做 fan-out（涵蓋清單內外＋自由問答衍生職涯，無需離線基建）。
- stage2 自身的進一步提速（目前 21–34s 可接受）。

## 7. 風險與權衡

- **每請求 6 支並行小呼叫** vs 原 2 支大呼叫：並行數上升，但每支短、快速釋放 RPM；預期**降**而非升 429。上線後以 Sentry 觀測。
- **首載仍 ~70s**：較 150s 大幅改善但非「秒開」；以 streaming stepper 緩解感知，並以 phase 2 快取進一步壓低。
- **池上限 ~30**：非硬限（可調參數 + 動態續池），但超過前 ~30 最相關課相關性遞減，30 為品質/成本甜蜜點。
