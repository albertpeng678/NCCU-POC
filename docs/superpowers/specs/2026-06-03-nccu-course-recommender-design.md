# NCCU 課程推薦系統 — 設計文件

**日期：** 2026-06-03  
**範圍：** 114學年度第二學期，2877 門課  
**目標：** 使用者輸入職涯目標，系統推薦最相關的政大課程清單（含課綱連結）

---

## 1. 系統概覽

開放給所有人使用、無驗證機制的課程推薦工具。使用者從 50 種預定義職涯中選擇目標，系統透過 Gemini File Search 進行語義檢索，回傳分三組的推薦課程卡片。

---

## 2. 架構

### 元件

| 元件 | 技術 | 部署 |
|------|------|------|
| Frontend | 純 HTML/CSS/JS SPA | Railway Static Site |
| Backend | Python FastAPI | Railway Docker |
| RAG Knowledge Base | Gemini File Search Store | Google AI |
| Ingestion Pipeline | Python 一次性腳本 | 本地執行（部署前） |

### 靜態資料檔（隨 backend code 部署）

- `career_skills.json` — 50 種職涯 → 技能關鍵字清單（人工定義）
- `courses_meta.json` — 2877 課 metadata（ingestion 時從 XLSX 生成）

---

## 3. 資料來源與 Ingestion Pipeline

### 課程清單取得

主要來源：`https://newdoc.nccu.edu.tw/teaschm/CoursesList.xlsx`（官方提供，無需爬蟲，共 2877 筆）

備用來源（XLSX 失效時）：按 16 個學院 code 分批呼叫 `https://es.nccu.edu.tw/course/zh-TW/:sem=1142%20:dp1={code}%20/` API 並合併去重。

### 課綱 URL 公式

課程代號 9 碼（如 `000211012`）→ URL：

```
https://newdoc.nccu.edu.tw/teaschm/1142/schmPrv.jsp-yy=114&smt=2&num={前6碼}&gop={第7-8碼}&s={第9碼}.html
```

課綱頁為 server-rendered HTML（無需 JS 執行），使用 httpx 加 `ssl.OP_LEGACY_SERVER_CONNECT` 可直接抓取。

### Ingestion 步驟（`ingestion/run.py`）

1. 下載 XLSX → 解析 2877 筆課程 → 建立 `courses_meta.json`
2. 對每門課（asyncio + semaphore 限制並行數，建議 10-20）：
   - 以 httpx 抓取課綱 HTML → BeautifulSoup 萃取文字
   - 失敗（timeout/SSL）→ retry 3 次（指數退避）→ 仍失敗 → `source: "name_only"`
   - 呼叫 Gemini 生成「技能橋接段落」附加至文字末尾（rate limit：60 QPM free tier）
3. 上傳每份文件至 Gemini File Search Store（附 `course_id`、`syllabus_url` 作為 custom_metadata）
4. 輸出 `FILE_SEARCH_STORE_NAME` 及 `failed_courses.json`

### 技能橋接段落格式（附加至課綱文字末尾）

```
=== 課程技能對應（自動生成）===
課程代號: {course_id}
培養技能：{skill_1}、{skill_2}、...
適合職涯：{career_1}、{career_2}、...
關鍵詞：{keyword_1}、{keyword_2}、...
```

此段落使課綱在語義搜尋中能被職涯相關 query 命中，補足學術詞彙與職場詞彙之間的語義落差。

---

## 4. 查詢流程（Runtime）

```
POST /recommend { "career": "產品經理(PM)" }
    │
    ├─ backend: career_skills.json lookup → skill_set string
    │
    ├─ [Stage 1] Gemini generate_content（file_search tool）
    │   prompt: "為職涯 {career}（技能：{skill_set}）找出最相關的 15 門課，
    │            每門課必須回傳其課程代號（格式：9位數字，如 000211012，出現在文件的「課程代號:」欄位）"
    │   → 回傳 top 15 候選課程（含 course_id、相關文字段落）
    │   → course_id 可從 skill bridge 段落的「課程代號: {id}」欄位取得
    │
    ├─ [Stage 2] Gemini generate_content（無 file_search，structured output）
    │   prompt: "從以下 15 門課中，為 {career} 目標選出 10 門並分組"
    │   schema: { groups: { core: [{course_id, reason}], supporting: [...], extended: [...] } }
    │   → SDK response_schema 強制格式，失敗自動 retry 3 次
    │
    └─ backend: 用 course_id join courses_meta.json → 補全 name/dept/teacher/credits/syllabus_url
       → 回傳最終 JSON
```

### 分組定義

| 群組 | 數量 | 說明 |
|------|------|------|
| `core` 核心技能 | 3–4 門 | 直接對應職涯核心能力 |
| `supporting` 輔助技能 | 3–4 門 | 強化周邊能力、增加競爭力 |
| `extended` 延伸視野 | 2–3 門 | 跨域拓展、差異化視角 |

### 多班次處理

同一門課（相同前 6 碼）可能有多個班（`gop` 不同）。Stage 2 prompt 明確指示：同前 6 碼只推薦一次，選 Stage 1 相關度最高的班次。

---

## 5. API 設計

### `POST /recommend`

**Request:**
```json
{ "career": "產品經理(PM)" }
```

**Response:**
```json
{
  "career": "產品經理(PM)",
  "groups": {
    "core": [
      {
        "course_id": "000211012",
        "name": "政治學",
        "department": "政治系",
        "teacher": "蔡中民",
        "credits": 3.0,
        "reason": "培養系統性分析框架，對應 PM 的結構化思考需求",
        "syllabus_url": "https://newdoc.nccu.edu.tw/teaschm/..."
      }
    ],
    "supporting": [...],
    "extended": [...]
  },
  "latency_ms": 3240
}
```

**錯誤回應：**
- `400` career 不在清單內
- `503` Gemini 無結果 / rate limit / structured output 連續失敗

---

## 6. 資料模型

### career_skills.json
```json
{
  "產品經理(PM)": {
    "label": "產品經理(PM)",
    "skills": ["產品規劃", "用戶研究", "需求分析", "數據分析", "跨部門溝通", "專案管理"],
    "description": "負責產品從概念到上線的完整規劃與執行"
  }
}
```
共 50 種職涯，人工定義，隨 backend code 版控。

### courses_meta.json
```json
{
  "000211012": {
    "name": "政治學",
    "name_en": "Political Science",
    "department": "政治系",
    "teacher": "蔡中民",
    "credits": 3.0,
    "kind": "必修",
    "syllabus_url": "https://newdoc.nccu.edu.tw/teaschm/1142/schmPrv.jsp-yy=114&smt=2&num=000211&gop=01&s=2.html",
    "source": "syllabus"
  }
}
```
`source` 欄位：`"syllabus"`（有完整課綱）或 `"name_only"`（僅用課名生成 skill tags）。

---

## 7. 錯誤處理

### Ingestion（一次性，可重跑）

| 情況 | 處理 |
|------|------|
| 課綱抓取失敗 | retry 3次 → 用課名生成 skill tags（`source: "name_only"`），記入 `failed_courses.json` |
| XLSX 下載失敗 | fallback：按學院 code 分批呼叫 API |
| Gemini skill tag 失敗 | 跳過 skill bridge，直接上傳原始文字 |

### Runtime

| 情況 | 處理 |
|------|------|
| career 不在清單 | HTTP 400（前端 autocomplete 已防守） |
| Stage 1 回傳 0 筆 | HTTP 503 + 錯誤訊息 |
| Stage 2 structured output 格式錯誤 | SDK 自動 retry，連續 3 次失敗 → HTTP 503 |
| course_id join 失敗 | 略過補位，從 Stage 1 剩餘取下一筆 |
| Gemini rate limit (429) | HTTP 503 + "系統繁忙，請稍後再試" |

---

## 8. Observability — Railway Postgres Logging

### query_log 表 Schema

```sql
CREATE TABLE query_log (
    id                      SERIAL PRIMARY KEY,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    career                  VARCHAR(100) NOT NULL,
    success                 BOOLEAN NOT NULL,
    latency_ms              INTEGER,
    stage1_count            INTEGER,
    result_core_count       INTEGER,
    result_supporting_count INTEGER,
    result_extended_count   INTEGER,
    error_type              VARCHAR(50),
    error_message           TEXT,
    judge_relevance_score   SMALLINT,
    judge_grouping_score    SMALLINT,
    judge_reason_score      SMALLINT,
    judge_diversity_score   SMALLINT,
    judge_overall_score     SMALLINT,
    judge_critique          TEXT,
    judge_evaluated_at      TIMESTAMPTZ
);
```

### 兩段式非阻塞寫入

**Phase 1**（立即，BackgroundTask）：request metadata 寫入，judge 欄位 NULL

**Phase 2**（~5-10秒後，同一 BackgroundTask 繼續）：Gemini judge 評分 → `UPDATE` row

### LLM Judge 評分維度

| 維度 | 定義 |
|------|------|
| `relevance` | 課程與職涯目標的整體相關程度（1-5） |
| `grouping` | 核心/輔助/延伸分組合理性（1-5） |
| `reason_quality` | 推薦原因是否具體可操作（1-5） |
| `diversity` | 跨系所/領域多樣性（1-5） |
| `overall` | 四維度 round 平均（1-5） |

Judge 失敗（Gemini 錯誤）不影響 Phase 1 log，欄位保持 NULL。

---

## 9. 前端 UX 設計（待 frontend-design skill 執行）

- 顏色：Navy blue 主色，上限 3 種顏色
- 無 emoji
- 輸入：autocomplete 搜尋框 + 6 個熱門職涯 pill（單擊即帶入）
- 結果：三組分組卡片，每張卡片含課程名、系所、推薦原因、「查看課綱」連結
- NNgroup 依據：use-case suggestion chips（single-click, specific > generic）

---

## 9. 專案結構

```
NCCU-poc/
├── ingestion/
│   ├── run.py              # 一次性執行入口
│   ├── scraper.py          # httpx 抓 2877 課綱 HTML
│   ├── skill_tagger.py     # Gemini 生成技能橋接段落
│   └── uploader.py         # 上傳 Gemini File Search Store
│
├── backend/
│   ├── main.py             # FastAPI app + CORS
│   ├── recommend.py        # 兩階段 Gemini pipeline
│   ├── career_skills.json
│   ├── courses_meta.json   # ingestion 生成後放入
│   └── Dockerfile
│
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── app.js              # CONFIG.API_URL 部署前手動填入
│
├── .env.example
├── .gitignore              # .env, courses_meta.json（可選）, __pycache__
└── railway.toml

Git remote: git@github.com:albertpeng678/NCCU-POC.git（使用 github-personal SSH key）
```

---

## 10. 環境變數

### Backend（Railway）

| 變數 | 取得方式 |
|------|---------|
| `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| `FILE_SEARCH_STORE_NAME` | ingestion/run.py 執行後 print 輸出 |
| `ALLOWED_ORIGIN` | Railway frontend 部署後取得的 URL |

### Frontend

`app.js` 頂部 `CONFIG.API_URL`：Railway backend 部署後手動填入。

### 部署順序

1. 本地跑 `ingestion/run.py` → 取得 `FILE_SEARCH_STORE_NAME`
2. Railway 部署 backend（先設 `ALLOWED_ORIGIN=*`）→ 取得 backend URL
3. 把 backend URL 填入 `frontend/app.js` → Railway 部署 frontend → 取得 frontend URL
4. 更新 backend `ALLOWED_ORIGIN` 為 frontend URL → redeploy backend

---

## 11. 測試策略

### Unit（pytest，無 Gemini call）
- career lookup：有效 / 無效 career
- metadata join：course_id 存在 / 不存在
- 課號 → URL 公式驗證

### Integration（手動，真實 Gemini call）
- 選 5 種職涯跑完整流程
- 確認三組分組存在、課綱連結可開啟
- 確認同一前 6 碼不重複出現

### 範疇外（POC）
- 推薦品質系統性評估（人工抽樣即可）
- 負載測試
- ingestion pipeline 自動化測試
