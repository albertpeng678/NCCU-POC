# Q&A 無 DB 優雅降級設計（in-memory ephemeral session）

> 設計文件（brainstorming 產出）。日期 2026-06-04。
> 目標：自由問答在「無 DATABASE_URL / DB 連不到」時，不再硬性 503，改用記憶體 ephemeral session 維持多輪問答；DB 在時行為完全不變（持久化 + judge）。

## 1. 問題與根因

**症狀**：自由問答出現 `查詢失敗：Cannot create session (DB unavailable)。請稍後再試。`

**根因（已驗證，逐行）**：
- `backend/db.py:init_pool` 在 `DATABASE_URL` 未設時早退 → `_pool = None`。
- `backend/qa_logger.py:create_session` 在 `pool is None` 時 `return None`（或 INSERT 失敗 return None）。
- `backend/main.py:237-238`（POST `/qa`）與 `:303-306`（GET `/qa/stream`）：`session_id is None` → 硬性 503 / SSE error。

**深層**：把「可選的持久化（跨請求/重啟保存 + judge 分析）」**耦合**成「必要的 session 識別碼」。識別碼其實只需一個 uuid，不需 DB。

**部署事實對齊（已實查 Railway）**：production 的 `Postgres` service RUNNING、backend 已設 `DATABASE_URL=${{Postgres.DATABASE_URL}}`（已解析）。故線上本有 DB；使用者看到的 503 幾乎確定來自**本機未帶 DATABASE_URL**。本降級為**防禦縱深**：本機 demo 免依賴 Railway proxy、DB 萬一抖動也不全掛。

## 2. 設計：in-memory ephemeral session fallback

新增進程內記憶體 session store（單例 dict），在 `pool is None` 時接管：

- **session 識別碼**：`create_session` 在無 DB 時回 `uuid4().hex`（或 `str(uuid4())`），並在 in-memory store 註冊空 session。
- **多輪狀態**：in-memory store 保存每個 session 的 turns（user/model 內容、interaction_id），供：
  - `prev_interaction_id`（多輪串接，main.py 用）
  - `build_history_from_turns`（重建對話歷史）
- **寫入**：`insert_turn` / `bump_session` 在無 DB 時寫進 in-memory store（取代原「pool 為 None 就跳過」——改為「寫記憶體」）。
- **讀回**：`get_session` / `get_session_turns` 在無 DB 時查 in-memory store；查無 → 視為新 session（不 raise、不 503）。

**DB 在時**：完全走原 asyncpg 路徑（持久化 + 背景 judge），in-memory store 不介入。

**降級邊界**：in-memory session 在進程重啟 / 多進程間不共享、`GET /qa/session/{id}` 對 ephemeral session 回空——皆可接受（PoC 單進程、短會話 demo）。

## 3. 元件改動（file:line 對照現況）

| 檔案 | 函式 | 改動 |
|---|---|---|
| `backend/qa_logger.py` | 新增 `_EphemeralStore`（module 單例）；`create_session`/`get_session`/`insert_turn`/`bump_session`/`get_session_turns` | `pool is None` 分支改走 in-memory（取代 return None / 跳過） |
| `backend/main.py` | POST `/qa`（~221-256）、GET `/qa/stream`（~283-320） | `create_session` 不再回 None → 移除硬性 503 路徑；session 解析改相容 in-memory |
| `backend/qa.py` | `build_history_from_turns`（純函式） | 確認可吃 in-memory turns 形狀（與 DB row 形狀一致即無需改） |

> 抽象原則：把「session 後端」抽成一個介面，DB pool 在→Postgres 實作，否則→in-memory 實作；main.py 不需判斷 pool。降低耦合、利於測試。

## 4. 錯誤處理

- DB 在但 query 中途失敗（連線斷）：捕捉例外 → 該請求降級為 ephemeral（或回可重試訊息），不裸 500。
- session_id 帶了但查無（DB 或記憶體都沒）：當新 session 開，不 503。

## 5. 測試（TDD，對應測試策略 §2.F / §3.3）

**單元（pytest，無 DB 分支）**：
- 無 DB create session → 回 uuid 格式、非 None。
- 無 DB get 剛建 session → 命中。
- 無 DB 多輪 turn1→2→3 → 順序保存、`build_history_from_turns` 正確重建。
- 無 DB get 不存在 session → None/新建，不 raise。
- session 隔離（不同 id 不串台）。
- **DB 在時行為不變**（回歸保護：仍寫/讀 Postgres）。
- `build_history_from_turns` 空輸入 → 空 history。
- in-memory store 每測獨立 + teardown 清空（避免 module-state flaky）。

**e2e（Playwright，有 DB / 無 DB 兩 project）**：
- with-DB：多輪問答 + `GET /qa/session/{id}` 讀回持久化。
- no-DB：同流程，session ephemeral、**不 503**、多輪上下文記憶體保留。
- no-DB reload：ephemeral 遺失屬可接受、不崩。
- 兩環境：離題引導、citations 空覆寫防幻覺、markdown 表格 XSS 清洗。

## 6. Out of Scope

- 跨進程 / 跨重啟的 ephemeral 持久化（PoC 不需）。
- DB 健康檢查 / 自動重連（可日後加，非本次）。
