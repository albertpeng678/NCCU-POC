# 串流即時進度 UX 設計（SSE）— 設計文件

> 日期：2026-06-04（Session 2）
> 狀態：設計定案，待寫實作計畫（writing-plans）
> 範圍：兩大功能模式的「等待體驗」升級——把目前「黑箱等 40-50s / 10-30s」改成**後端真實階段的即時串流回報**。

---

## 一、目標與動機

使用者最大痛點：**等待時不知道系統在幹嘛、等很躁**。

兩個功能各自升級：

1. **職涯推薦（/recommend）**：把「校準模擬的 2 步驟等待 UX」升級成 **真 SSE 5 階段即時進度**——每個階段亮燈是後端真的做完那一步。
2. **自由問答（/qa）**：答案改成 **真串流打字機**——逐字出現、速度可控（不要太快），首字最快出現以降低感知等待。

**核心原則：誠實。** 進度反映後端真實工作，永不謊報完成。

### 非目標（YAGNI）
- 不追求把推薦的「實際總時長」變短（那是另一條線：HANDOFF「零」的 stage1+2 合併）。本案是**感知優化 + 真實透明**。
- 不做 WebSocket（SSE 對「伺服器單向推進度」已足夠，且更簡單）。
- 不重做多樣性 / 清單外職涯邏輯（沿用現有 pipeline）。

---

## 二、關鍵架構決策（已和使用者確認）

| 決策 | 選擇 | 理由 |
|------|------|------|
| 推薦進度傳輸 | **真 SSE + 保留舊 `POST /recommend` 當 fallback** | proxy 擋串流時可降級成模擬版，上線最穩 |
| 問答串流 | **改用 `generate_content_stream` + file_search**（async `client.aio`） | Context7 證實可逐字串流；放棄 Interactions API 的伺服器端多輪歷史，改自行帶入歷史 |
| 超時行為 | **停在該階段 ~90% + 換文案「正在做最後整理…」** | NN/G 實證最不焦慮；永不謊報完成 |
| 打字速度 | **中速（~34 字/秒）** | 使用者親自挑選 |
| 阻塞問題 | 全程用 SDK 原生 **async（`client.aio`）** | 同步阻塞會凍住 event loop、心跳停擺 |

### SSE 做功課發現的坑與對策（實作必須遵守）
| 坑 | 對策 |
|----|------|
| `EventSource` 不能帶 POST body | 推薦串流用 **`GET /recommend/stream?career=..&seed=..`**（輸入非敏感，GET OK，原生支援+自動重連） |
| Railway/nginx proxy 緩衝吃掉串流 | sse-starlette/FastAPI 送 `X-Accel-Buffering: no`；**心跳 ping 每 15s** 防 idle timeout；部署後用 `curl -N` 驗證逐塊到達 |
| 斷線自動重連 → 整條 pipeline 重跑（燒 Gemini 錢） | 前端收到最終 `result`/`error`/`no_match` 事件就 **`es.close()`**；後端偵測 `request.is_disconnected()` 中止 |
| markdown 邊串流邊渲染半截爆版 | **逐「完整區塊」顯示**：累積 raw 文字，節流（~80ms）重渲染，未閉合的 `**`/表格列先藏，閉合才顯示 |
| 打字速度比閱讀快 | **緩衝 + 固定節奏吐字**（與 token 到達速度脫鉤），中速 34 cps |

---

## 三、推薦模式：5 階段（誠實對應後端真實工作）

| # | 使用者看到 | 後端真實工作（recommend.py） | SSE 事件觸發時機 | 備註 |
|---|-----------|------------------------------|------------------|------|
| 1 | 理解你的職涯方向 | 決定技能：清單內=查 `career_skills.json`（即時）／清單外=`derive_skills_for_career`（AI 推導） | 技能決定完 | 清單外此步是真 AI、停較久 |
| 2 | 檢索全校課綱 🐢 | `stage1_retrieve`（File Search 海選 POOL_SIZE 門） | 海選回傳 | 慢步驟 |
| 3 | 篩選候選課程 | `deduplicate_by_name` + `sample_candidates`（純程式） | 抽樣完成 | 快 |
| 4 | 編排推薦組合與理由 🐢 | `stage2_group`（分組 + 生成理由） | 分組回傳 | 慢步驟 |
| 5 | 整理課程資訊 | `join_metadata`（補課名/老師/連結）+ dedup | 即將回傳 | 快 |

🐢 = 真正花時間的 AI 步驟。

### SSE 事件協定（`GET /recommend/stream`）
```
event: stage     data: {"n":1,"key":"understand","status":"start"}
event: stage     data: {"n":1,"key":"understand","status":"done"}
... (每階段 start/done) ...
event: result    data: {<完整 RecommendResponse JSON>}      # 成功
event: no_match  data: {"career":"...","message":"..."}      # 清單外推不出/檢索空
event: error     data: {"error_type":"...","message":"..."}  # Gemini 503 等
: keep-alive                                                  # 心跳，每 15s
```
- 前端收到 `result`/`no_match`/`error` → **立即 `es.close()`**（防重連重跑）。
- 每個 `stage done` 事件 → 前端把該階段標記完成、進度條躍進到該階段對應 %。

### 前端進度條行為（融合真實事件 + 平滑）
- 真實「階段邊界」由 SSE 事件驅動（誠實）。
- **階段內部**用校準 easing 平滑推進到「該階段配額的 ~90%」就停住等真事件（避免卡頓 + 超時安全網）。
- 收到下一個 `stage done` → 補滿該段、躍進下一段。
- 全部 `result` 到 → 進度條補滿 100% + 轉綠 + 呈現結果。

### Fallback 降級
- 前端優先連 `GET /recommend/stream`。
- 若 SSE 連線失敗 / 首個事件逾時（例如 proxy 緩衝）→ 自動改打舊 `POST /recommend`，並退回**校準模擬**的 5 階段動畫（不中斷使用者）。

---

## 四、問答模式：真串流打字機

### 後端（qa.py 改寫）
- 從 Interactions API → **`client.aio.models.generate_content_stream`** + `file_search` tool。
- **多輪記憶**：不再靠 `previous_interaction_id`；改由後端把先前輪次（question/answer）組進 `contents` 帶入（資料來源：qa_turn 或前端傳入的精簡歷史）。
- **citations**：串流結束後從最終 chunk 的 grounding metadata 萃取（沿用現有 `extract_course_ids_from_grounding` 概念）。
- **防幻覺**：citations 空時仍覆寫「查無資料」（沿用現有規則）。
- 端點：**`GET /qa/stream?question=..&session_id=..`**（SSE）。舊 `POST /qa` 保留當 fallback。

### SSE 事件協定（`GET /qa/stream`）
```
event: token     data: {"text":"…部分文字…"}     # 多次，逐塊
event: done      data: {"citations":[...],"followups":[...],"session_id":"...","turn":N}
event: error     data: {"error_type":"...","message":"..."}
: keep-alive
```

### 前端打字機（markdown 安全）
- 累積 raw markdown 文字。
- **節流渲染**（~80ms tick）：每次用 marked + DOMPurify 重渲染，但只渲染到「最後一個完整區塊」邊界（雙換行/表格結尾）；尾端未閉合的 inline（半個 `**`）先藏。
- **固定節奏吐字**：中速 ~34 cps，與 token 到達速度脫鉤（token 快則進緩衝、慢則等）。
- 打字游標（cyan 閃爍）；打完字後才淡入 citations + followup chips。
- 表格：整塊淡入（不逐字），避免半截爆版。

### Fallback
- SSE 失敗 → 改打舊 `POST /qa` 拿完整答案 → 前端**收完再逐字播放**（client-side typewriter，給到視覺、零後端依賴）。

---

## 五、情境與邊界（驗收必涵蓋）

| 情境 | 推薦 | 問答 |
|------|------|------|
| 清單內職涯 | 階段 1 秒過，2/4 為主 | — |
| 清單外職涯 | 階段 1 為真 AI（停較久） | — |
| no_match（推不出/檢索空） | `no_match` 事件 → 溫和卡片導向問答，進度優雅停止不假完成 | — |
| Gemini 503 / 錯誤 | `error` 事件 → 當前階段標錯誤色 + 重試訊息 | 同左，打字中斷顯示錯誤 |
| 換一批（reroll） | 同串流流程、帶新 seed | — |
| 超時（階段比估計久） | 停該段 ~90% + 換文案 | 打字機自然等 token |
| 多輪追問 | — | 帶入歷史、串流接續 |
| SSE 被 proxy 擋 | 降級 POST + 模擬 | 降級 POST + client 打字機 |

---

## 六、後端改動清單

- `backend/requirements.txt`：加 `sse-starlette`。
- `backend/recommend.py`：stage 函式改 async（`client.aio.*`）；抽出可被串流產生器逐段呼叫的結構（保留舊同步版供 `POST /recommend` fallback，或同源 async 化）。
- `backend/qa.py`：改 `generate_content_stream` + file_search；多輪歷史帶入；citations 串流末萃取。
- `backend/main.py`：新增 `GET /recommend/stream`、`GET /qa/stream`（`EventSourceResponse`，`ping=15`，偵測 `is_disconnected`）；Sentry 包覆串流例外；保留舊 `POST` 端點。
- CORS：SSE 端點同樣套 `ALLOWED_ORIGIN`。

## 七、前端改動清單

- `frontend/app.js`：
  - 推薦：`EventSource(/recommend/stream)` + 階段事件處理 + easing + 90%-hold + 完成補滿 + `es.close()` + fallback 偵測。
  - 問答：`EventSource(/qa/stream)` + 緩衝 + 節流 markdown 安全渲染 + 固定節奏吐字 + 游標 + 打完淡入 citations/followup + fallback client 打字機。
  - `CONFIG`：串流端點 URL（沿用 API_URL 衍生）。
- `frontend/style.css`：5 階段 stepper、打字游標、表格淡入、live 點等樣式；RWD 確保小螢幕不破版。
- `frontend/index.html`：載入結構容器調整；資源 `?v=N` cache-bust bump。

---

## 八、測試與驗收標準（使用者硬性要求）

> SSE 坑多，**必須真測過、Sentry 真的沒在噴錯**才算完成。

1. **Playwright skill + Playwright MCP 完整 E2E**（真後端、真 Gemini）：
   - 推薦：清單內、清單外、no_match、503 錯誤、換一批——5 階段事件逐一亮、進度條行為正確、完成呈現。
   - 問答：單輪、多輪追問、citations、followup、離題、錯誤——打字機逐字、表格不爆版、打完才出引用。
2. **跨裝置跨尺寸驗收**：手機 / 平板 / 桌機 viewport 下，串流與打字機**不破版、不卡頓、可讀**（截圖存證）。
3. **Sentry MCP 驗證**：串流全程（含正常與錯誤路徑）Sentry **無非預期錯誤**；預期錯誤（503）有被正確捕捉分類。
4. **降級驗證**：模擬 SSE 失敗 → fallback POST + 模擬/client 打字機正常。
5. **發現問題即修**，修完重跑驗證（verification-before-completion）。

### 量測注意
- 乾淨延遲量測需避開 Gemini 429 速率牆（勿短時間連續大量打）。
- 部署後用 `curl -N <stream-url>` 確認逐塊到達（非一次到 = proxy 在緩衝）。

---

## 九、開放問題
- 多輪歷史帶入的「精簡程度」（帶幾輪、是否摘要）→ 實作時依 token 成本微調，預設帶最近 2-3 輪原文。
- Railway 是否完全尊重 `X-Accel-Buffering: no` → 部署後實測，必要時調整 service proxy 設定。
