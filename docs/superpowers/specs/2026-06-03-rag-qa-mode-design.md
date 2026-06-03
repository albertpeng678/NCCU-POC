# RAG 自由問答模式 — 設計文件

**日期：** 2026-06-03
**範圍：** 在現有課程推薦系統上，新增第二個模式：基於課綱知識庫的多輪 RAG 問答
**前提：** Plan 1-4 已完成（ingestion、backend 兩階段 Gemini、logging、frontend）；Gemini File Search Store 已建立並可複用

---

## 1. 概覽

現有系統有「職涯 → 課程組合推薦」一個模式。本設計新增「自由問答」模式：使用者直接問任意課程相關問題（如「哪些課教 Python？」「政治系有什麼選修？」），系統基於同一個課綱知識庫做 grounded RAG 問答，支援多輪連續對話與 citations。

兩模式透過輸入框上方的 **scoped chips 切換**（NNgroup 可見模式原則）。

---

## 2. UI 設計

### 模式切換（依 NNgroup [Modes](https://www.nngroup.com/articles/modes/) + [Prompt Controls](https://www.nngroup.com/articles/prompt-controls-genai/)）

輸入框上方放兩個 scoped chip，可見、使用者主動選：

```
[● 職涯推薦]  [ 自由問答 ]
┌─────────────────────────────────┐
│ 輸入框（placeholder 隨模式變）   │
└─────────────────────────────────┘
```

- **職涯推薦模式**（現有）：autocomplete + 熱門 pills → 三組課程卡
- **自由問答模式**（新）：自由文字輸入，placeholder「問任何關於課程的問題…」→ 答案 + citations + 可連續追問

反對後端自動判斷意圖（隱形模式違反 mode 可見性，誤判時使用者困惑）。

### 問答結果呈現

- AI 答案（markdown 渲染，沿用結構化排版風格）
- 「參考課綱」區：citations 列出引用到的課程（課名 + 可點課綱連結）
- **追問建議 chips**（NNgroup「facilitating followups」實證：77% 對話 >1 輪）：答案氣泡底部顯示 2-3 個動態生成的相關追問 chip，單擊即填入 composer 並送出
- 多輪：答案下方保留輸入框，可繼續追問；對話氣泡式堆疊（user / assistant 交替）
- 「新對話」按鈕：清空當前 session，開新 session

---

## 3. 多輪對話機制（Gemini File Search 原生）

採 Gemini **Interactions API** 的 server 端狀態管理：

```
第 1 輪：client.interactions.create(model, input=問題, tools=[file_search])
        → 回傳 interaction1.id + 答案 + grounding_metadata
第 2 輪：client.interactions.create(model, input=追問,
                                    previous_interaction_id=interaction1.id,
                                    tools=[file_search])
        → Gemini server 自動帶入對話歷史
```

對話歷史存在 Gemini server，後端只需保存 `last_interaction_id` 字串供接續。本系統另在 Postgres 持久化對話內容供分析與重整讀回（見 §5）。

---

## 4. API 設計

### `POST /qa`

**Request:**
```json
{
  "question": "哪些課程會教到 Python 程式設計？",
  "session_id": "uuid-or-null"
}
```
- `session_id` 為 null → 後端建立新 session
- 帶既有 session_id → 接續對話（用該 session 的 last_interaction_id）

**Response:**
```json
{
  "session_id": "uuid",
  "turn_number": 2,
  "answer": "根據課綱，以下課程涵蓋 Python：...",
  "citations": [
    {"course_id": "000211012", "name": "資料分析與決策", "syllabus_url": "https://..."}
  ],
  "followup_suggestions": ["需要先修統計嗎？", "這門課幾學分？", "還有其他系開的嗎？"],
  "latency_ms": 4200
}
```

**錯誤回應：**
- `400` question 空白 / session_id 格式錯
- `404` session_id 不存在
- `503` Gemini 失敗 / file_search 無結果

### `GET /qa/session/{session_id}`（重整讀回對話歷史）
回傳該 session 所有 turn（question/answer/citations，依 turn_number 排序）。

---

## 5. 資料模型（Postgres）

```sql
-- 對話 session（一個 session = 一連串多輪問答）
CREATE TABLE qa_session (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    last_interaction_id TEXT,             -- Gemini previous_interaction_id
    turn_count          INTEGER DEFAULT 0
);

-- 每一輪問答（turn）
CREATE TABLE qa_turn (
    id              SERIAL PRIMARY KEY,
    session_id      UUID REFERENCES qa_session(id) ON DELETE CASCADE,
    turn_number     INTEGER NOT NULL,     -- 該 session 內第幾輪（1-based）
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    question        TEXT NOT NULL,
    answer          TEXT,
    citation_count  INTEGER,
    citations_json  JSONB,
    followup_json   JSONB,               -- 追問建議 chips（2-3 個字串）
    latency_ms      INTEGER,
    success         BOOLEAN NOT NULL,
    error_type      VARCHAR(50),
    error_message   TEXT,
    -- Q&A 專用 LLM judge（async 第二階段寫入）
    judge_faithfulness  SMALLINT,         -- 1-5，答案忠於課綱（RAG 最關鍵）
    judge_relevancy     SMALLINT,         -- 1-5，答案切題
    judge_context_prec  SMALLINT,         -- 1-5，檢索課綱相關性
    judge_overall       SMALLINT,         -- 1-5，加權平均
    judge_critique      TEXT,
    judge_evaluated_at  TIMESTAMPTZ
);

CREATE INDEX idx_qa_turn_session ON qa_turn(session_id);
CREATE INDEX idx_qa_turn_created ON qa_turn(created_at DESC);
CREATE INDEX idx_qa_turn_judge ON qa_turn(judge_overall);
```

`turn_count` 在每次成功 turn 後 `UPDATE qa_session SET turn_count = turn_count + 1`，讓「同 session 幾輪對話」一查即得。

---

## 6. 兩階段流程（沿用 recommend 模式的 fire-and-forget 哲學）

```
POST /qa
  │
  ├─ session_id null → INSERT qa_session（新 UUID）
  │  否則 → SELECT last_interaction_id FROM qa_session
  │
  ├─ Gemini interactions.create(input=question, file_search tool,
  │                             previous_interaction_id=last_interaction_id)
  │  → answer + grounding_metadata（citations）+ new interaction_id
  │  → followup_suggestions：prompt 指示 Gemini 回答後生成 2-3 個相關追問
  │     （File Search tool 不能配 response_schema，故追問與答案一起以文字產生後解析，
  │      或第二次無 tool 的輕量 call 生成——實作時擇一，優先單次文字解析）
  │
  ├─ 從 grounding_metadata 萃取 course_id（來源：grounding chunk 對應的上傳檔
  │  display_name = "course-{id}"，或回退解析答案/chunk 文字中的「課程代號:」9碼）
  │  → join courses_meta.json 補課綱 URL；查無則該 citation 略過

  │
  ├─ Phase 1（立即）：INSERT qa_turn（含 turn_number）；
  │                   UPDATE qa_session SET last_interaction_id, turn_count+1
  │  → 回傳 response 給前端
  │
  └─ Phase 2（背景，BackgroundTasks）：Q&A judge 評 3 維 → UPDATE qa_turn judge 欄位
```

---

## 7. LLM Judge for Q&A（[RAGAS](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/) reference-free 標準）

維度（各 1-5）：
| 維度 | 定義 | 生產門檻參考 |
|------|------|------------|
| `faithfulness` | 答案是否忠於課綱、無幻覺（RAG 最關鍵） | 客戶面向 >0.85（→ 4-5 分） |
| `answer_relevancy` | 答案是否直接回應問題 | >0.8 |
| `context_precision` | 檢索到的課綱對問題的相關度 | >0.8 |
| `overall` | 三維 round 平均 | — |

Judge 失敗不影響 Phase 1，欄位保持 NULL（同 recommend judge 行為）。Q&A judge 與 recommend judge 是**不同 judge**（維度不同），各自獨立。

---

## 8. 檔案結構（新增/修改）

```
backend/
├── qa.py            # 新增：Q&A pipeline（interactions API、citation 萃取）
├── qa_judge.py      # 新增：Q&A 專用 LLM judge（3 維）
├── qa_logger.py     # 新增：qa_session / qa_turn 寫入與讀回
├── main.py          # 修改：加 POST /qa、GET /qa/session/{id}
├── models.py        # 修改：加 QaRequest / QaResponse / Citation
└── schema.sql       # 修改：加 qa_session / qa_turn 表

frontend/
├── index.html       # 修改：加模式切換 chips、問答結果區、對話氣泡
├── app.js           # 修改：模式切換邏輯、/qa 呼叫、session_id 保存、citation 渲染
└── style.css        # 修改：chips、對話氣泡、citation 樣式
```

---

## 9. 營運分析查詢（docs/analytics.sql 追加）

```sql
-- 每個 session 進行幾輪對話
SELECT id AS session_id, turn_count, created_at
FROM qa_session ORDER BY turn_count DESC;

-- 平均每 session 對話輪數
SELECT ROUND(AVG(turn_count), 1) AS avg_turns_per_session FROM qa_session;

-- Q&A 品質趨勢（faithfulness 最關鍵）
SELECT ROUND(AVG(judge_faithfulness),2) AS faithfulness,
       ROUND(AVG(judge_relevancy),2) AS relevancy,
       ROUND(AVG(judge_context_prec),2) AS context_prec,
       ROUND(AVG(judge_overall),2) AS overall,
       COUNT(*) AS judged
FROM qa_turn WHERE judge_overall IS NOT NULL;

-- 低 faithfulness（潛在幻覺）turn，供人工審查
SELECT created_at, question, answer, judge_faithfulness, judge_critique
FROM qa_turn WHERE judge_faithfulness IS NOT NULL
ORDER BY judge_faithfulness ASC LIMIT 20;

-- 錯誤率
SELECT COUNT(*) total, SUM(CASE WHEN success THEN 0 ELSE 1 END) errors
FROM qa_turn;
```

---

## 10. 測試策略

### Unit（pytest，無 Gemini）
- citation 萃取：grounding_metadata → course_id → join courses_meta
- qa judge 解析：JSON → 1-5 clamp + overall
- turn_number 遞增邏輯
- session 建立 / 接續分支

### Integration（手動，真實 Gemini + Postgres）
- 新 session 問答 → 答案 + citations
- 同 session 追問 → Gemini 帶入上下文（驗證 previous_interaction_id 生效）
- 重整讀回 GET /qa/session
- turn_count 正確遞增
- judge async 寫入

### E2E（Playwright，跨裝置）
- 模式切換 chips
- 問答輸入 → 答案 + citation 連結可點
- 多輪追問氣泡堆疊
- 行動版問答版面

### 範疇外（POC）
- 對話歷史分頁
- 跨使用者帳號（無登入）
```
