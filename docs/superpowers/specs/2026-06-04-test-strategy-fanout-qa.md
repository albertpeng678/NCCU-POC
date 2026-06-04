# 測試策略：推薦 fan-out 重構 + Q&A 無 DB 降級

> 高覆蓋 / 壓測 / edge case 測試策略。方法論基礎：playwright-skill。
> 受測對象 A：fan-out + 整池預標註 + 前端分頁換一批；B：Q&A in-memory ephemeral session 降級。

## 1. 金字塔分層原則

- **純函式、無 I/O**（合併/去重/分頁/ranked 轉換/in-memory session）→ pytest 單元（多、快、窮舉）。
- **跨邊界**（前端↔自家 backend↔DB↔真實 RAG）→ Playwright e2e（少、貴、關鍵流程）。
- **禁 mock 自家 success path**（when-to-mock.md：Never mock your own frontend-to-backend）。允許 mock 的唯一邊界：第三方（Sentry/CDN）、以及用 `page.route fulfill({status:429/503})` 注入錯誤。
- fan-out 每請求 6 支並行小檢索會燒 RPM → e2e 樣本刻意稀少（職涯 ≤2），窮舉留 pytest。

## 2. 單元測試清單（pytest）

### A. fan-out 合併去重
- A1 6 技能各回 ~5 課 → 合併成單一扁平 list、保留 course_id。
- A2 兩技能重疊 id → 去重後僅一筆（先到保留）。
- A3 跨掛同名課（同名不同 id）→ `deduplicate_by_name` 去同名。
- A4 `deduplicate_by_prefix`（前 6 碼）相同 → 收斂一筆。
- A5 `correct_candidate_ids` 修正抄錯/截斷 id；修不回的丟棄不崩。
- A6 合併採 round-robin 交錯（技能1[0],技能2[0]…）以保多樣。

### B. 單技能失敗容錯
- B1 6 支中 1 支 raise（503/timeout）→ gather 不整體失敗、該支貢獻 []、其餘正常。
- B2 1 支回空 → 池由其餘組成、不誤判 no_match。
- B3 **全部**皆空/皆失敗 → 空池 → 上層轉 no_match（清單外）/ error（清單內）。
- B4 1 支非法 JSON → 視為 []、不污染他支。

### C. 池上限交錯
- C1 合併 > POOL_TARGET(30) → 截斷前 30、round-robin 後取。
- C2 = 30 → 全保留。
- C3 < batch_size(10)（如 7）→ 全保留、不報錯、不補零。
- C4 截斷後仍多技能來源、非單技能霸佔。

### D. 扁平 ranked 轉換（取代巢狀 groups）
- D1 整池每課都有 group∈{core,supporting,extended} + rank。
- D2 rank 由高到低、同 rank 有確定 tie-break（不隨機）。
- D3 `join_metadata` 逐課接 meta；缺 meta 丟棄、不帶 None。
- D4 reason={lead, points:[{term,detail}]}、≥2 粗體 term。
- D5 標註後仍無重複 id、無跨掛同名。
- D6 `RecommendResponse` 扁平 courses + batch_size 預設 10 + 回 seed。

### E. 前端分頁切批（純邏輯，建議抽 JS module 單測；否則由 e2e §3.2 覆蓋）
- E1 30 課池 → 第 1 批 rank 1–10。
- E2 換一批 → 11–20、shown 推進。
- E3 再換 → 21–30、池乾。
- E4 第 4 次換（池乾）→ 回「需續池」訊號（觸發新 seed `/recommend`），不空白不崩。
- E5 池=7（<batch）→ 首批全 7；換一批 → 續池或「已涵蓋主要推薦」。
- E6 每批內 group 歸區塊（同批 10 門依 group 分三區）。
- E7 續池 append 與既有 shown 去重（不重看同課）。

### F. in-memory session（Q&A 降級）
- F1 無 DB create → uuid 格式、非 None、非 503。
- F2 無 DB get 剛建 → 命中。
- F3 無 DB 多輪 turn1→2→3 順序保存、`build_history_from_turns` 正確重建。
- F4 無 DB get 不存在 → None/新建、不 raise、不 503。
- F5 不同 session_id 隔離不串台。
- F6 **DB 在行為不變**（回歸保護：仍寫/讀 Postgres）。
- F7 `build_history_from_turns` 空輸入 → 空 history。
- 紀律：每測獨立 session_id + teardown 清空 store（避免 module-state flaky）。

## 3. e2e 測試清單（Playwright real backend）

共用：baseURL 在 config、webServer 同起 backend(:8000)+frontend(:3000)、role/data-attr locator、web-first 斷言、reducedMotion、首載 setTimeout 120s、desktop+mobile+tablet 三 project（device profile 含 hasTouch）。

### R. 推薦 user flow
- R1 清單內職涯 → SSE 5 階段依序、首批 ≤10 卡可見、首載 < ~80s（事件等待非固定延遲）。
- R2 第 1 批依 group 分流三區（landmark + toHaveCount）。
- R3 每卡 lead + ≥2 粗體 bullet + 課綱連結有 href。
- R4 清單外職涯（記者）→ 有結果出卡 / 推不出溫和導向問答。

### S. 換一批切片 + 池乾續池（最核心、最易 flaky）
- S1 點換一批 → **無新網路請求**（request 計數不增）→ 顯示 11–20、≈瞬間。
- S2 第 2 批首卡 id ≠ 第 1 批首卡。
- S3 連點兩次 → 21–30、池乾。
- S4 池乾再點 → **此時才發** `/recommend`（新 seed）；waitForResponse 註冊於 click 前。
- S5 rapid double-click → 不重複切、不跳批、不崩（處理中 disable/去抖）。
- S6 reload → 回首批初始態、不殘半批、無 null.classList。

### Q. Q&A 多輪（有 DB / 無 DB 兩 project）
- Q1 with-DB：問→答（markdown 表格、citations、followup）→ 追問 2、3 輪沿用上下文 → `GET /qa/session/{id}` 讀回。
- Q2 no-DB：同流程、session ephemeral、**不 503**、多輪記憶體保留。
- Q3 no-DB reload：ephemeral 遺失可接受、不崩。
- Q4 both：離題引導；citations 空覆寫防幻覺。
- Q5 both：markdown XSS 清洗（`<script>` 不執行）。

### M. RWD（橫切 R/S/Q）
- M1 mobile：卡單欄堆疊、**無水平捲動**、換一批可 tap。
- M2 mobile：Q&A 表格不溢出。
- M3 tablet：三區塊版面正確、reroll icon 不變形。
- M4 desktop：完整呈現基準。

## 4. Edge cases（16 項，標層）
空檢索(B3/e2e)、單技能空(B2)、全技能失敗(B3/e2e)、清單外職涯(derive/e2e R4)、池<10(C3/E5)、池=10(E1)、池=30(C2/E1-3)、換一批超過池(E4/S4)、重複 id(A2/A4/D5)、跨掛同名(A3/D5)、SSE 斷線 fallback 到 POST(e2e)、429/503 注入(單元退避+e2e route.fulfill)、DB 突斷(e2e+F)、session 不存在(F4/e2e)、特殊字元/XSS(e2e Q5)、超長職涯字串(e2e)。

## 5. 壓力測試方法
- 並行 N=3/5/8 個 `/recommend`（不同職涯），記狀態碼/latency/空池；有效並行=N×6（fan-out）。
- 與功能 e2e 隔離（tag `@stress`、低頻、不進 PR gate，避免污染量測—CLAUDE.md 慣例）。
- **通過門檻**：429 率 < 改前基線（必須下降）；503 率 <2%；p50 首載 <~70s、p95 <~90s；換一批 <200ms（0 請求）；空池率 ≈0；latency 無貼 180s 尾巴。
- 對照：同腳本跑「改前單次大檢索」vs「fan-out」同職涯同並行度，證明提速與 429 下降非噪音。

## 6. 穩定度 Flake Gate
- **5x consecutive 0-flake**：關鍵 e2e（R1, S1–S4, Q1, Q2）`--repeat-each=5` 全綠才過；換一批/續池額外 `--repeat-each=10` burn-in。
- CI retries=2（偵測非掩蓋）、本機 retries=0；trace on-first-retry；fullyParallel；forbidOnly。
- role/data-attr locator、web-first 斷言、零 waitForTimeout、module-state 每測隔離、SSE 用 expect.poll/toPass。

## 7. Sentry MCP 觀測點（上線後）
project `nccu-poc` @ org `albert-ar`。查：429 事件率（須下降、無貼 180s 尾巴）、無新「Stage 1 無候選」issue、單技能失敗不外溢成 user-facing error、`/qa` 無 503 風暴、stage2 503 兜底比率、前端無 null.classList/EventSource/切片越界例外。上線後 24h 與 7d 各查一次。

## 8. 優先級
- **Critical（擋 merge）**：B1–B3 容錯、F1–F6 無 DB 不 503、Q5 XSS、S4 池乾續池。
- **High**：A1–A6、C1–C3、D1–D6、R1–R2、S1–S3、Q1–Q2。
- **Medium**：SSE fallback、429/503 注入、E4/E5、M1–M4、D3。
- **Low**：history 空輸入、reason 格式細節、reroll icon 視覺。
