# 清單外職涯處理設計（Open Career Handling）

> 日期：2026-06-04
> 狀態：設計定案（user 核准），待實作 + Playwright AC 驗證
> 相關：`backend/recommend.py`、`backend/main.py`、`backend/models.py`、`frontend/app.js`、`frontend/index.html`、`frontend/style.css`

## 一句話

讓使用者輸入「不在收錄 50 種」的職涯（如清潔工）時不再卡死：能推導就用 LLM 即時推導**可轉移能力**並以**真實檢索**推薦（誠實標明），真的查無則溫和導向問答模式——全程不捏造、不留死路（NNgroup）。

## 問題

目前 recommend 硬限 50 種職涯：前端打清單外職涯無法送出（按鈕停用），後端回 400。使用者「清潔工」直接撞死路。

## 設計

### 後端
1. **`derive_skills_for_career(client, career) -> list[str] | None`**（新增於 recommend.py）
   - 用 Gemini 為任意職涯推導 5-8 個**可轉移／學術可教**的技能關鍵字（例：清潔工 → 衛生管理、公共衛生、職場安全、基礎管理、人際溝通；而非「拖地、清潔劑」這類教不了的）。
   - 回 `None` 當輸入非真實職涯（亂打、無意義）。
2. **`build_recommendation_instrumented(..., skills=None)`**：新增選用 `skills` 參數。給定時用之，否則照舊從 `career_skills.json` 載入。
3. **stage1 誠實回空**：`stage1_retrieve` prompt 加一句「若知識庫沒有任何課程與這些技能真正相關，回傳空陣列 `[]`，不要硬湊不相關的課」。
4. **`/recommend` 流程**：
   - career ∈ 50 → 靜態技能（現狀，AC1 不回歸）。
   - career ∉ 50 → `derive_skills_for_career`：
     - 推導出技能 → 用該技能檢索：
       - 檢索到真實課程 → 正常推薦，並在 response 帶 `notice`（誠實說明：「政大沒有直接對應『清潔工』的課程，但以下課程能培養相關的**可轉移能力**：」）。
       - 檢索空 → 回 `no_match`（建議改問答）。
     - 推導不出（None） → 回 `no_match`。
   - **保證**：所有課程卡來自真實 stage1/stage2 檢索，零捏造。`notice` 僅為框架說明文字。
5. **`models.py`**：`RecommendResponse` 加 `notice: str | None = None`。新增 `NoMatchResponse`（或在既有回應用 `no_match: bool`）。採法：`/recommend` 在 no_match 時回 **HTTP 200** + `{"career":..., "no_match": true, "message": "...導去問答..."}`（避免 4xx 被前端當錯誤）。為此 response_model 放寬為 `RecommendResponse | NoMatchResponse`（FastAPI Union）。

### 前端
1. **放寬送出**：輸入框非空即可送（`searchBtn` 不再僅在選到 50 種時啟用）。送出時用輸入框當前文字當 career。
2. **`notice` 顯示**：`renderResults` 若 `data.notice` 非空，在課程卡上方顯示一段誠實說明條（樣式區別於一般標題）。
3. **`no_match` 處理**：若回應 `no_match` → 顯示溫和訊息卡：「『清潔工』目前沒有對應課程，你可以用**問答模式**問我相關方向 →」+ 按鈕一鍵 `setMode("qa")` 並把職涯帶入問答輸入框。
4. 自動完成仍推薦 50 種（recognition over recall），但允許清單外輸入。

## 驗收條件（AC）— 必須以 Playwright 實際驗證

- **AC1**：50 種內職涯推薦行為不回歸（仍出 core/supporting/extended 卡）。
- **AC2**：清單外、可直接對應的職涯 → 推導技能 → 真實課程卡正常顯示。
- **AC3**：清單外、需轉移的職涯（清潔工）→ 顯示 `notice` 誠實說明 + **真實**可轉移能力課程卡。
- **AC4**：真的查無 / 亂打 → 溫和 `no_match` 訊息 + 一鍵導去問答，**不卡死**。
- **AC5**：前端清單外輸入**可以送出**（按鈕不再死鎖）。
- **AC6**：所有課程卡皆來自真實檢索（抽查 course_id 存在於 courses_meta）。

> Playwright E2E（真實後端 + full store）驗 AC2-AC5；AC1 防回歸；AC6 以斷言課程卡的課綱連結/course_id 為真。需 5x 連續綠。

## 測試策略
- 單元（不需 live Gemini，mock client）：`derive_skills_for_career` 解析、`build_recommendation_instrumented(skills=...)` 用傳入技能、stage1 空→no_match 分支、models `notice`/`no_match` 序列化。
- Live：full store + 真實 Gemini，Playwright E2E 跑 AC2-AC5。

## 非目標
- 不擴充 50 種靜態清單（改用 LLM 動態推導）。
- 不做職涯拼字糾正/同義詞（未來）。
