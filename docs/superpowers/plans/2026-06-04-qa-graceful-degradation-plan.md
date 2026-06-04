# Q&A 無 DB 優雅降級（in-memory ephemeral session）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓自由問答（`POST /qa` 與 `GET /qa/stream`）在 `DATABASE_URL` 未設或 DB 連不到時，不再硬性回 503，改用進程內記憶體 ephemeral session 維持多輪上下文；DB 在時行為完全不變（asyncpg 持久化 + 背景 judge）。

**Architecture:** 在 `backend/qa_logger.py` 內新增一個 module 單例 `_EphemeralStore`，並把現有五個 session 函式（`create_session` / `get_session` / `insert_turn` / `bump_session` / `get_session_turns`）的 `pool is None` 分支從「return None / 跳過」改為「走 in-memory store」。函式 signature **完全不變**（仍是 `(pool, ...)`），所以 `main.py` 的呼叫端幾乎不動——只移除兩處 `session_id is None → 503` 的死路徑。pool 在時所有函式照走原 asyncpg 路徑，ephemeral store 不被觸碰。`build_history_from_turns` 是純函式，in-memory turn 形狀刻意與 DB row 形狀對齊（`{question, answer, success}`），故不需改它。

**Tech Stack:** Python 3.11、FastAPI、asyncpg（DB 路徑）、pytest + pytest-asyncio（單元）、FastAPI TestClient（API 層）、Playwright real backend（e2e，有 DB / 無 DB 兩 project）。`uuid.uuid4().hex` 產生 ephemeral session id。

---

## File Structure（本計畫涉及的檔案與職責）

| 檔案 | 動作 | 職責 |
|---|---|---|
| `backend/qa_logger.py` | Modify | 新增 `_EphemeralStore` 單例 + `reset_ephemeral_store()`；五個 session 函式的 `pool is None` 分支改走記憶體 |
| `backend/main.py` | Modify | 移除 `POST /qa`（行 237-238）與 `GET /qa/stream`（行 302-306）的 `session_id is None → 503/SSE error` 死路徑 |
| `tests/backend/test_qa_ephemeral.py` | Create | F1–F7 純單元（pool=None 走記憶體、pool 在回歸不變、隔離、空輸入、teardown） |
| `tests/backend/test_qa_ephemeral_api.py` | Create | API 層：`POST /qa` 與 `GET /qa/stream` 在 `get_pool()→None` 時不 503（用 TestClient + monkeypatch） |
| `frontend/e2e/qa.spec.ts` | Create | Playwright Q1–Q5 多輪問答 + XSS + 防幻覺（有 DB / 無 DB 兩 project 共用） |
| `frontend/playwright.config.ts` | Modify/Create | 新增 `qa-with-db` 與 `qa-no-db` 兩 project，webServer 帶/不帶 `DATABASE_URL` |

> 抽象原則落地：把「session 後端」收斂在 `qa_logger.py` 一個檔案內（pool 在→Postgres、否則→`_EphemeralStore`），`main.py` 不需要知道哪一種後端在跑。

---

## Task 1: `_EphemeralStore` 單例與 `reset_ephemeral_store`（測試隔離基礎設施）

**Files:**
- Modify: `backend/qa_logger.py`（在檔頭 import 區之後、`create_session` 之前插入）
- Test: `tests/backend/test_qa_ephemeral.py`（Create）

- [ ] **Step 1: Write the failing test**

建立 `tests/backend/test_qa_ephemeral.py`，內容如下（本 task 只放 store-level 測試；後續 task 會 append 更多測試到同檔）：

```python
# tests/backend/test_qa_ephemeral.py
"""F1-F7：Q&A 無 DB ephemeral session 降級（純單元）。
pool=None → 走 module 單例 _EphemeralStore；pool 在 → 回歸不變（不碰 store）。
紀律：每測 teardown 清空 store，避免 module-state flaky。
"""
import re
import pytest

from backend import qa_logger
from backend.qa_logger import (
    _EphemeralStore,
    reset_ephemeral_store,
    create_session,
    get_session,
    insert_turn,
    bump_session,
    get_session_turns,
    build_history_from_turns,
)


@pytest.fixture(autouse=True)
def _clean_store():
    """每測前後清空 module 單例，杜絕跨測污染。"""
    reset_ephemeral_store()
    yield
    reset_ephemeral_store()


# ---------- store 基礎：註冊/讀回/隔離/清空 ----------

def test_ephemeral_store_register_and_get():
    store = _EphemeralStore()
    sid = store.create()
    assert store.get(sid) == {"last_interaction_id": None, "turn_count": 0}


def test_ephemeral_store_get_unknown_returns_none():
    store = _EphemeralStore()
    assert store.get("does-not-exist") is None


def test_reset_ephemeral_store_clears_module_singleton():
    sid = qa_logger._STORE.create()
    assert qa_logger._STORE.get(sid) is not None
    reset_ephemeral_store()
    assert qa_logger._STORE.get(sid) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/backend/test_qa_ephemeral.py -v`
Expected: FAIL with `ImportError: cannot import name '_EphemeralStore'`（store 與 `reset_ephemeral_store` 尚未定義）。

- [ ] **Step 3: Write minimal implementation**

在 `backend/qa_logger.py` 的 `import json` / `from typing import Optional` 之後、`async def create_session` 之前插入：

```python
import uuid


class _EphemeralStore:
    """進程內記憶體 session 後端：DB 不可用時接管多輪 session 狀態。

    結構：
      _sessions[sid] = {"last_interaction_id": str|None, "turn_count": int}
      _turns[sid]    = list[dict]  # 每筆形狀對齊 DB row：{question, answer, success, ...}
    降級邊界：進程重啟 / 多進程不共享、純記憶體（PoC 可接受）。
    """

    def __init__(self) -> None:
        self._sessions: dict[str, dict] = {}
        self._turns: dict[str, list[dict]] = {}

    def create(self) -> str:
        sid = uuid.uuid4().hex
        self._sessions[sid] = {"last_interaction_id": None, "turn_count": 0}
        self._turns[sid] = []
        return sid

    def get(self, session_id: str) -> Optional[dict]:
        sess = self._sessions.get(session_id)
        return dict(sess) if sess is not None else None

    def add_turn(self, session_id: str, turn: dict) -> int:
        # 查無 → 視為新 session（不 raise）：對齊「session_id 帶了但查無 → 當新 session 開」。
        if session_id not in self._sessions:
            self._sessions[session_id] = {"last_interaction_id": None, "turn_count": 0}
            self._turns[session_id] = []
        self._turns[session_id].append(turn)
        return len(self._turns[session_id])  # 充當 turn_id（>0、非 None）

    def bump(self, session_id: str, interaction_id: str) -> None:
        if session_id not in self._sessions:
            self._sessions[session_id] = {"last_interaction_id": None, "turn_count": 0}
            self._turns[session_id] = []
        self._sessions[session_id]["last_interaction_id"] = interaction_id
        self._sessions[session_id]["turn_count"] += 1

    def turns(self, session_id: str) -> list[dict]:
        return list(self._turns.get(session_id, []))


# module 單例：無 DB 時所有 session 函式共用這一份記憶體。
_STORE = _EphemeralStore()


def reset_ephemeral_store() -> None:
    """測試用：重建單例，杜絕跨測 module-state 污染。"""
    global _STORE
    _STORE = _EphemeralStore()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/backend/test_qa_ephemeral.py -v`
Expected: PASS（3 個 store 基礎測試綠）。

- [ ] **Step 5: Commit**

```bash
git add backend/qa_logger.py tests/backend/test_qa_ephemeral.py
git commit -m "feat(qa): add in-memory _EphemeralStore singleton for DB-less sessions

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `create_session` 無 DB 走記憶體（F1）

**Files:**
- Modify: `backend/qa_logger.py:8-19`（`create_session`）
- Test: `tests/backend/test_qa_ephemeral.py`（append）

- [ ] **Step 1: Write the failing test**

Append 到 `tests/backend/test_qa_ephemeral.py`：

```python
# ---------- F1：無 DB create → uuid 格式、非 None、非 503 ----------

_HEX32 = re.compile(r"^[0-9a-f]{32}$")


@pytest.mark.asyncio
async def test_create_session_no_db_returns_uuid():
    sid = await create_session(None)          # pool=None → 走 ephemeral
    assert sid is not None
    assert _HEX32.match(sid), f"expected uuid4().hex, got {sid!r}"
    # 已註冊進 store → 後續可讀回
    assert qa_logger._STORE.get(sid) == {"last_interaction_id": None, "turn_count": 0}


@pytest.mark.asyncio
async def test_create_session_no_db_unique_ids():
    a = await create_session(None)
    b = await create_session(None)
    assert a != b
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/backend/test_qa_ephemeral.py -k create_session_no_db -v`
Expected: FAIL（現行 `create_session(None)` 回 `None` → `assert sid is not None` 失敗）。

- [ ] **Step 3: Write minimal implementation**

把 `backend/qa_logger.py` 的 `create_session` 改為：

```python
async def create_session(pool) -> Optional[str]:
    """Insert a new qa_session row. Returns session id as str.

    無 DB（pool is None）→ 回 ephemeral uuid 並註冊進 _STORE（不再回 None）。
    DB 在但 INSERT 失敗 → 仍回 None（上層另行處理）。
    """
    if pool is None:
        return _STORE.create()
    try:
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "INSERT INTO qa_session DEFAULT VALUES RETURNING id::text"
            )
    except Exception as e:
        print(f"[qa_logger] create_session failed: {e}")
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/backend/test_qa_ephemeral.py -k create_session_no_db -v`
Expected: PASS（2 測綠）。

- [ ] **Step 5: Commit**

```bash
git add backend/qa_logger.py tests/backend/test_qa_ephemeral.py
git commit -m "feat(qa): create_session returns ephemeral uuid when DB absent (F1)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: `get_session` 無 DB 查記憶體 + 查無回 None（F2、F4 一半）

**Files:**
- Modify: `backend/qa_logger.py:22-37`（`get_session`）
- Test: `tests/backend/test_qa_ephemeral.py`（append）

- [ ] **Step 1: Write the failing test**

```python
# ---------- F2：無 DB get 剛建 session → 命中 ----------

@pytest.mark.asyncio
async def test_get_session_no_db_hits_just_created():
    sid = await create_session(None)
    sess = await get_session(None, sid)
    assert sess == {"last_interaction_id": None, "turn_count": 0}


# ---------- F4（一半）：無 DB get 不存在 → None（不 raise、不 503）----------

@pytest.mark.asyncio
async def test_get_session_no_db_unknown_returns_none_no_raise():
    sess = await get_session(None, "ffffffffffffffffffffffffffffffff")
    assert sess is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/backend/test_qa_ephemeral.py -k get_session_no_db -v`
Expected: FAIL（現行 `get_session(None, sid)` 直接回 `None` → `test_get_session_no_db_hits_just_created` 失敗）。

- [ ] **Step 3: Write minimal implementation**

把 `backend/qa_logger.py` 的 `get_session` 改為：

```python
async def get_session(pool, session_id: str) -> Optional[dict]:
    """Fetch session row. Returns {last_interaction_id, turn_count} or None.

    無 DB → 查 _STORE；查無回 None（上層把 None 當「新 session 開」，不 raise/不 503）。
    """
    if pool is None:
        return _STORE.get(session_id)
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT last_interaction_id, turn_count FROM qa_session WHERE id=$1::uuid",
                session_id,
            )
            if row is None:
                return None
            return dict(row)
    except Exception as e:
        print(f"[qa_logger] get_session failed: {e}")
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/backend/test_qa_ephemeral.py -k get_session_no_db -v`
Expected: PASS（2 測綠）。

- [ ] **Step 5: Commit**

```bash
git add backend/qa_logger.py tests/backend/test_qa_ephemeral.py
git commit -m "feat(qa): get_session reads ephemeral store when DB absent (F2/F4)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: `insert_turn` 無 DB 寫記憶體（F3 寫入半 + turn_id 非 None）

**Files:**
- Modify: `backend/qa_logger.py:40-90`（`insert_turn`）
- Test: `tests/backend/test_qa_ephemeral.py`（append）

- [ ] **Step 1: Write the failing test**

```python
# ---------- F3（寫入）：無 DB insert_turn → 寫進 store，回非 None turn_id ----------

@pytest.mark.asyncio
async def test_insert_turn_no_db_writes_store_and_returns_id():
    sid = await create_session(None)
    result = {
        "answer": "政治學不錯",
        "citations_course_ids": ["000211012"],
        "followup_suggestions": ["要不要看國際關係？"],
        "latency_ms": 1234,
    }
    tid = await insert_turn(None, sid, 1, "問政治學", result, None)
    assert tid is not None and tid > 0
    turns = await get_session_turns(None, sid)
    assert len(turns) == 1
    assert turns[0]["question"] == "問政治學"
    assert turns[0]["answer"] == "政治學不錯"
    assert turns[0]["success"] is True


@pytest.mark.asyncio
async def test_insert_turn_no_db_failed_turn_marks_success_false():
    sid = await create_session(None)
    tid = await insert_turn(None, sid, 1, "壞問題", None, RuntimeError("boom"))
    assert tid is not None
    turns = await get_session_turns(None, sid)
    assert turns[0]["success"] is False
    assert turns[0]["answer"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/backend/test_qa_ephemeral.py -k insert_turn_no_db -v`
Expected: FAIL（現行 `insert_turn(None, ...)` 回 `None` → `assert tid is not None` 失敗）。

- [ ] **Step 3: Write minimal implementation**

把 `backend/qa_logger.py` 的 `insert_turn` 開頭 `if pool is None: return None` 改為「組出 turn dict 寫 store」。完整改寫如下（只改 `pool is None` 分支與把欄位計算上移共用）：

```python
async def insert_turn(
    pool,
    session_id: str,
    turn_number: int,
    question: str,
    result: Optional[dict],
    error: Optional[Exception],
) -> Optional[int]:
    """Insert a qa_turn row. result is the answer_question dict or None.
    Returns the new row id, or None on failure.

    無 DB → 把對齊 DB row 形狀的 turn dict 寫進 _STORE，回 store 內序號（>0、非 None）。
    """
    success = error is None and result is not None
    answer = result.get("answer") if result else None
    citations = result.get("citations_course_ids", []) if result else []
    citation_count = len(citations)
    citations_json = json.dumps(citations) if citations else None
    followup = result.get("followup_suggestions", []) if result else []
    followup_json = json.dumps(followup) if followup else None
    latency_ms = result.get("latency_ms") if result else None
    error_type = type(error).__name__ if error else None
    error_message = str(error)[:500] if error else None

    if pool is None:
        # 形狀對齊 DB row（含 build_history_from_turns 需要的 question/answer/success）。
        turn = {
            "session_id": session_id,
            "turn_number": turn_number,
            "question": question,
            "answer": answer,
            "citation_count": citation_count,
            "citations_json": citations,
            "followup_json": followup,
            "latency_ms": latency_ms,
            "success": success,
            "error_type": error_type,
            "error_message": error_message,
        }
        return _STORE.add_turn(session_id, turn)

    try:
        async with pool.acquire() as conn:
            return await conn.fetchval(
                """INSERT INTO qa_turn (
                    session_id, turn_number, question,
                    answer, citation_count, citations_json, followup_json,
                    latency_ms, success, error_type, error_message
                ) VALUES (
                    $1::uuid, $2, $3,
                    $4, $5, $6::jsonb, $7::jsonb,
                    $8, $9, $10, $11
                ) RETURNING id""",
                session_id,
                turn_number,
                question,
                answer,
                citation_count,
                citations_json,
                followup_json,
                latency_ms,
                success,
                error_type,
                error_message,
            )
    except Exception as e:
        print(f"[qa_logger] insert_turn failed: {e}")
        return None
```

> 注意：欄位計算（success/answer/...）原本在 `try` 內，現上移到函式頂端供兩路徑共用。DB 路徑行為不變（同樣的值、同樣的 SQL）。`citations_json` 在 DB 路徑仍是 JSON 字串；store 路徑直接存 list（記憶體不需序列化）。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/backend/test_qa_ephemeral.py -k insert_turn_no_db -v`
Expected: PASS（2 測綠）。

- [ ] **Step 5: Commit**

```bash
git add backend/qa_logger.py tests/backend/test_qa_ephemeral.py
git commit -m "feat(qa): insert_turn writes ephemeral store when DB absent (F3)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: `bump_session` + `get_session_turns` 無 DB 走記憶體 + 多輪重建（F3 完整）

**Files:**
- Modify: `backend/qa_logger.py:93-108`（`bump_session`）、`:150-165`（`get_session_turns`）
- Test: `tests/backend/test_qa_ephemeral.py`（append）

- [ ] **Step 1: Write the failing test**

```python
# ---------- F3（完整多輪）：turn1→2→3 順序保存 + build_history 正確重建 + bump 推進 turn_count ----------

@pytest.mark.asyncio
async def test_multi_turn_no_db_preserves_order_and_bumps():
    sid = await create_session(None)
    for i in (1, 2, 3):
        res = {"answer": f"A{i}", "citations_course_ids": [],
               "followup_suggestions": [], "latency_ms": 0}
        await insert_turn(None, sid, i, f"Q{i}", res, None)
        await bump_session(None, sid, f"interaction-{i}")

    sess = await get_session(None, sid)
    assert sess["turn_count"] == 3
    assert sess["last_interaction_id"] == "interaction-3"

    turns = await get_session_turns(None, sid)
    assert [t["question"] for t in turns] == ["Q1", "Q2", "Q3"]

    history = build_history_from_turns(turns)
    assert history == [
        {"question": "Q1", "answer": "A1"},
        {"question": "Q2", "answer": "A2"},
        {"question": "Q3", "answer": "A3"},
    ]


@pytest.mark.asyncio
async def test_get_session_turns_no_db_unknown_returns_empty():
    assert await get_session_turns(None, "nope-nope-nope") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/backend/test_qa_ephemeral.py -k "multi_turn_no_db or get_session_turns_no_db" -v`
Expected: FAIL（現行 `bump_session(None,...)` 直接 return、`get_session_turns(None,...)` 回 `[]` → turn_count/順序斷言失敗）。

- [ ] **Step 3: Write minimal implementation**

把 `bump_session` 改為：

```python
async def bump_session(pool, session_id: str, interaction_id: str) -> None:
    """Update session last_interaction_id and increment turn_count.

    無 DB → 推進 _STORE 內的 turn_count 與 last_interaction_id。
    """
    if pool is None:
        _STORE.bump(session_id, interaction_id)
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE qa_session
                   SET last_interaction_id=$1, turn_count=turn_count+1
                   WHERE id=$2::uuid""",
                interaction_id,
                session_id,
            )
    except Exception as e:
        print(f"[qa_logger] bump_session failed: {e}")
```

把 `get_session_turns` 改為：

```python
async def get_session_turns(pool, session_id: str) -> list[dict]:
    """Fetch all turns for a session ordered by turn_number.

    無 DB → 回 _STORE 內該 session 的 turns（插入順序即 turn_number 順序）；查無回 []。
    """
    if pool is None:
        return _STORE.turns(session_id)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM qa_turn
                   WHERE session_id=$1::uuid
                   ORDER BY turn_number ASC""",
                session_id,
            )
            return [dict(row) for row in rows]
    except Exception as e:
        print(f"[qa_logger] get_session_turns failed: {e}")
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/backend/test_qa_ephemeral.py -k "multi_turn_no_db or get_session_turns_no_db" -v`
Expected: PASS（2 測綠）。

- [ ] **Step 5: Commit**

```bash
git add backend/qa_logger.py tests/backend/test_qa_ephemeral.py
git commit -m "feat(qa): bump_session + get_session_turns use ephemeral store when DB absent (F3)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: session 隔離（F5）+ `build_history_from_turns` 空輸入（F7）

**Files:**
- Test only: `tests/backend/test_qa_ephemeral.py`（append）— 行為已由 Task 1-5 實作；本 task 鎖定隔離與空輸入不回歸

- [ ] **Step 1: Write the failing test**

```python
# ---------- F5：不同 session_id 隔離不串台 ----------

@pytest.mark.asyncio
async def test_sessions_no_db_are_isolated():
    a = await create_session(None)
    b = await create_session(None)
    await insert_turn(None, a, 1, "Qa", {"answer": "Aa", "citations_course_ids": [],
                      "followup_suggestions": [], "latency_ms": 0}, None)
    await bump_session(None, a, "ia")

    # b 完全不受 a 影響
    assert await get_session_turns(None, b) == []
    assert (await get_session(None, b))["turn_count"] == 0
    # a 自己有 1 輪
    assert len(await get_session_turns(None, a)) == 1
    assert (await get_session(None, a))["turn_count"] == 1


# ---------- F7：build_history_from_turns 空輸入 → 空 history ----------

def test_build_history_empty_input_returns_empty():
    assert build_history_from_turns([]) == []
```

- [ ] **Step 2: Run test to verify it fails (then pass)**

Run: `python -m pytest tests/backend/test_qa_ephemeral.py -k "isolated or build_history_empty" -v`
Expected: 這兩測在 Task 1-5 實作後**應直接 PASS**（行為已具備）。若任一 FAIL → 代表隔離或空輸入有 bug，回頭修對應 store 方法（`add_turn`/`bump` 的 `session_id not in` 防呆、或 `_STORE.turns` 的 `.get(..., [])`）再續。這是回歸鎖。

- [ ] **Step 3: （無新實作）**

本 task 純粹鎖定既有行為，不新增 production 程式碼。

- [ ] **Step 4: Run full ephemeral suite**

Run: `python -m pytest tests/backend/test_qa_ephemeral.py -v`
Expected: PASS（F1–F7 store/no-DB 全綠）。

- [ ] **Step 5: Commit**

```bash
git add tests/backend/test_qa_ephemeral.py
git commit -m "test(qa): lock session isolation (F5) and empty-history (F7) for ephemeral store

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: DB 在時行為不變回歸保護（F6）

**Files:**
- Test only: `tests/backend/test_qa_ephemeral.py`（append）— 用 fake asyncpg pool 斷言「pool 在 → 走 SQL、不碰 store」

- [ ] **Step 1: Write the failing test**

```python
# ---------- F6：DB 在時行為不變（回歸保護：走 asyncpg、ephemeral store 不被觸碰）----------

class _FakeConn:
    """記錄被呼叫的 SQL，模擬 asyncpg connection。"""
    def __init__(self, store):
        self._store = store

    async def fetchval(self, sql, *args):
        self._store["calls"].append(("fetchval", sql))
        if "INSERT INTO qa_session" in sql:
            return "11111111-1111-1111-1111-111111111111"
        if "INSERT INTO qa_turn" in sql:
            return 42
        return None

    async def fetchrow(self, sql, *args):
        self._store["calls"].append(("fetchrow", sql))
        return {"last_interaction_id": "prev-x", "turn_count": 2}

    async def fetch(self, sql, *args):
        self._store["calls"].append(("fetch", sql))
        return [{"question": "Q1", "answer": "A1", "success": True, "turn_number": 1}]

    async def execute(self, sql, *args):
        self._store["calls"].append(("execute", sql))


class _FakeAcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    """最小 asyncpg pool：pool.acquire() 回 async context manager 給出 _FakeConn。"""
    def __init__(self):
        self.log = {"calls": []}
        self._conn = _FakeConn(self.log)

    def acquire(self):
        return _FakeAcquireCtx(self._conn)


@pytest.mark.asyncio
async def test_db_present_uses_asyncpg_not_ephemeral():
    pool = _FakePool()

    sid = await create_session(pool)
    assert sid == "11111111-1111-1111-1111-111111111111"

    sess = await get_session(pool, sid)
    assert sess == {"last_interaction_id": "prev-x", "turn_count": 2}

    tid = await insert_turn(pool, sid, 3, "Q",
                            {"answer": "A", "citations_course_ids": [],
                             "followup_suggestions": [], "latency_ms": 0}, None)
    assert tid == 42

    await bump_session(pool, sid, "intr-3")
    turns = await get_session_turns(pool, sid)
    assert turns == [{"question": "Q1", "answer": "A1", "success": True, "turn_number": 1}]

    # 確認真的走了 SQL（每路徑都有 call），且 ephemeral store 全程沒被寫入
    sqls = " | ".join(s for _, s in pool.log["calls"])
    assert "INSERT INTO qa_session" in sqls
    assert "INSERT INTO qa_turn" in sqls
    assert "UPDATE qa_session" in sqls
    assert qa_logger._STORE.get(sid) is None  # store 未被觸碰
    assert qa_logger._STORE.turns(sid) == []
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python -m pytest tests/backend/test_qa_ephemeral.py -k db_present -v`
Expected: PASS（Task 2-5 的 `if pool is None` 守門已保證 pool 在時不碰 store；此測是回歸鎖）。若 FAIL → 代表某函式誤把 DB 路徑導向 store，回頭修。

- [ ] **Step 3: （無新實作）**

純回歸保護，不新增 production 程式碼。

- [ ] **Step 4: Run full ephemeral suite once more**

Run: `python -m pytest tests/backend/test_qa_ephemeral.py -v`
Expected: PASS（F1–F7 全綠）。

- [ ] **Step 5: Commit**

```bash
git add tests/backend/test_qa_ephemeral.py
git commit -m "test(qa): regression-lock DB-present path uses asyncpg not ephemeral (F6)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: `POST /qa` 移除 `session_id is None → 503` 死路徑

**Files:**
- Modify: `backend/main.py:235-238`
- Test: `tests/backend/test_qa_ephemeral_api.py`（Create）

- [ ] **Step 1: Write the failing test**

建立 `tests/backend/test_qa_ephemeral_api.py`：

```python
# tests/backend/test_qa_ephemeral_api.py
"""API 層：/qa 與 /qa/stream 在無 DB（get_pool→None）時不 503，走 ephemeral session。
不 mock 自家 backend success path 的協調邏輯；只 mock 第三方 Gemini 呼叫（answer_question/stream_answer）
與 metadata 載入。pool=None 經由 monkeypatch get_pool 模擬「DATABASE_URL 未設」。
"""
import json
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from backend import qa_logger


@pytest.fixture(autouse=True)
def _clean_store():
    qa_logger.reset_ephemeral_store()
    yield
    qa_logger.reset_ephemeral_store()


@pytest.fixture
def client():
    from backend.main import app
    return TestClient(app)


def _events(text):
    out, cur = [], None
    for line in text.splitlines():
        if line.startswith("event:"):
            cur = line[len("event:"):].strip()
        elif line.startswith("data:"):
            out.append((cur, line[len("data:"):].strip()))
    return out


# ---------- POST /qa：無 DB → 不 503、回 ephemeral session_id ----------

def test_post_qa_no_db_does_not_503(client):
    fake_result = {
        "answer": "政治學 **不錯**",
        "citations_course_ids": [],
        "followup_suggestions": ["要不要看國際關係？"],
        "interaction_id": "intr-1",
        "latency_ms": 100,
    }
    with patch("backend.main.get_pool", return_value=None), \
         patch("backend.main.answer_question", return_value=fake_result), \
         patch("backend.main.load_courses_meta", return_value={}), \
         patch("backend.main.extract_citations_by_name", return_value=[]):
        resp = client.post("/qa", json={"question": "問政治學"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_id"]            # 非空 ephemeral id
    assert body["turn_number"] == 1


def test_post_qa_no_db_multi_turn_keeps_context(client):
    """無 DB 第二輪帶回首輪建立的 ephemeral session_id → turn_number 遞增、不 404/503。"""
    fake_result = {
        "answer": "答案", "citations_course_ids": [],
        "followup_suggestions": [], "interaction_id": "intr",
        "latency_ms": 0,
    }
    with patch("backend.main.get_pool", return_value=None), \
         patch("backend.main.answer_question", return_value=fake_result), \
         patch("backend.main.load_courses_meta", return_value={}), \
         patch("backend.main.extract_citations_by_name", return_value=[]):
        r1 = client.post("/qa", json={"question": "Q1"})
        sid = r1.json()["session_id"]
        r2 = client.post("/qa", json={"question": "Q2", "session_id": sid})
    assert r2.status_code == 200, r2.text
    assert r2.json()["session_id"] == sid
    assert r2.json()["turn_number"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/backend/test_qa_ephemeral_api.py -k post_qa_no_db -v`
Expected: 在改 main.py 前——`test_post_qa_no_db_does_not_503` 其實**已可能 PASS**（因 Task 2 讓 `create_session(None)` 回 uuid，不再觸發 503）。但 `test_post_qa_no_db_multi_turn_keeps_context` 仍可能因 main.py 既有結構而通過。為確保本 task 有 RED：先暫時把 main.py 行 236 改回 `session_id = None`（模擬舊行為）跑一次看 503 → 確認測試能抓到回歸；再還原。

> 實務做法：subagent 先跑 `-k post_qa_no_db`。若已綠（因 Task 2 已修 create_session），則本 task 的價值是「移除現在已無法到達的 dead 503 分支」+ 把該保護寫成回歸測。仍需執行 Step 3 清掉 dead code，並跑全套確認不回歸。

- [ ] **Step 3: Write minimal implementation**

把 `backend/main.py` 的 `POST /qa` 中這段：

```python
    else:
        session_id = await create_session(pool)
        if session_id is None:
            raise HTTPException(status_code=503, detail="Cannot create session (DB unavailable)")
```

改為：

```python
    else:
        # create_session 在無 DB 時回 ephemeral uuid（不再回 None）→ 移除硬性 503 死路徑。
        session_id = await create_session(pool)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/backend/test_qa_ephemeral_api.py -k post_qa_no_db -v`
Expected: PASS（2 測綠，無 503/404）。

- [ ] **Step 5: Commit**

```bash
git add backend/main.py tests/backend/test_qa_ephemeral_api.py
git commit -m "feat(qa): remove POST /qa DB-unavailable 503; use ephemeral session

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 9: `GET /qa/stream` 移除 `session_id is None → SSE error` 死路徑

**Files:**
- Modify: `backend/main.py:300-306`
- Test: `tests/backend/test_qa_ephemeral_api.py`（append）

- [ ] **Step 1: Write the failing test**

```python
# ---------- GET /qa/stream：無 DB → 不發 ServiceUnavailable error 事件，正常 done ----------

def test_qa_stream_no_db_does_not_emit_service_unavailable(client):
    async def _gen_ok():
        yield {"event": "token", "data": {"text": "政治學"}}
        yield {"event": "done", "data": {"course_ids": [], "answer_text": "政治學 **不錯**"}}

    with patch("backend.main.get_pool", return_value=None), \
         patch("backend.main.stream_answer", return_value=_gen_ok()), \
         patch("backend.main.load_courses_meta", return_value={}), \
         patch("backend.main.extract_citations_by_name", return_value=[]):
        resp = client.get("/qa/stream?question=問政治學")
    assert resp.status_code == 200
    evs = _events(resp.text)
    kinds = [e for e, _ in evs]
    assert "done" in kinds
    # 不得出現「DB unavailable」error 事件
    assert all("DB unavailable" not in d for _, d in evs)
    done = [json.loads(d) for e, d in evs if e == "done"][0]
    assert done["session_id"]            # 非空 ephemeral id
    assert done["turn_number"] == 1


def test_qa_stream_no_db_multi_turn_increments(client):
    """無 DB 第二輪帶首輪 ephemeral session_id → turn_number=2（記憶體保留上下文）。"""
    def _gen():
        async def g():
            yield {"event": "done", "data": {"course_ids": [], "answer_text": "答案"}}
        return g()

    with patch("backend.main.get_pool", return_value=None), \
         patch("backend.main.stream_answer", side_effect=lambda *a, **k: _gen()), \
         patch("backend.main.load_courses_meta", return_value={}), \
         patch("backend.main.extract_citations_by_name", return_value=[]):
        r1 = client.get("/qa/stream?question=Q1")
        sid = [json.loads(d) for e, d in _events(r1.text) if e == "done"][0]["session_id"]
        r2 = client.get(f"/qa/stream?question=Q2&session_id={sid}")
    done2 = [json.loads(d) for e, d in _events(r2.text) if e == "done"][0]
    assert done2["session_id"] == sid
    assert done2["turn_number"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/backend/test_qa_ephemeral_api.py -k qa_stream_no_db -v`
Expected: 同 Task 8——Task 2 修了 `create_session` 後，`session_id is None` 分支已不可達，這些測可能已綠。若已綠，本 task 仍須執行 Step 3 移除 dead SSE-error 分支並保留回歸測。若要驗證 RED：暫時把 main.py 行 301 改 `session_id = None` 跑一次見 ServiceUnavailable error 事件，再還原。

- [ ] **Step 3: Write minimal implementation**

把 `backend/main.py` 的 `GET /qa/stream` 中這段：

```python
    else:
        session_id = await create_session(pool)
        if session_id is None:
            async def _err():
                yield _sse("error", {"error_type": "ServiceUnavailable",
                                     "message": "Cannot create session (DB unavailable)"})
            return EventSourceResponse(_err(), ping=15)
```

改為：

```python
    else:
        # create_session 無 DB → ephemeral uuid（不再回 None）→ 移除 DB-unavailable SSE error 死路徑。
        session_id = await create_session(pool)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/backend/test_qa_ephemeral_api.py -k qa_stream_no_db -v`
Expected: PASS（2 測綠，無 ServiceUnavailable 事件）。

- [ ] **Step 5: Commit**

```bash
git add backend/main.py tests/backend/test_qa_ephemeral_api.py
git commit -m "feat(qa): remove /qa/stream DB-unavailable SSE error; use ephemeral session

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 10: 後端全套回歸 + ephemeral 多輪歷史串接驗證

**Files:**
- Test only：跑全套 pytest 確認既有 78+ 測試（含 `test_qa_stream_persist.py`、`test_qa.py`）不回歸

- [ ] **Step 1: Add ephemeral history-into-prompt assertion**

Append 到 `tests/backend/test_qa_ephemeral_api.py`，驗證「無 DB 多輪時，前一輪歷史確實透過 `build_history_from_turns` 餵進 stream_answer 的 `history` 參數」（這是 ephemeral 多輪上下文真正生效的關鍵串接點）：

```python
def test_qa_stream_no_db_feeds_prior_history_to_model(client):
    """第二輪：stream_answer 收到的 history 應含第一輪 (Q1,A1)，證明 ephemeral 上下文有串進 prompt。"""
    captured = {}

    def _stream_factory(client_, store, question, history):
        captured["history"] = history
        async def g():
            yield {"event": "done", "data": {"course_ids": [], "answer_text": "回答"}}
        return g()

    with patch("backend.main.get_pool", return_value=None), \
         patch("backend.main.stream_answer", side_effect=_stream_factory), \
         patch("backend.main.load_courses_meta", return_value={}), \
         patch("backend.main.extract_citations_by_name", return_value=[]):
        r1 = client.get("/qa/stream?question=Q1")
        sid = [json.loads(d) for e, d in _events(r1.text) if e == "done"][0]["session_id"]
        captured.clear()
        client.get(f"/qa/stream?question=Q2&session_id={sid}")

    assert captured["history"] == [{"question": "Q1", "answer": "回答"}]
```

> 註：`stream_answer` 的呼叫簽名是 `stream_answer(_client, _STORE_NAME, question, history)`（見 `backend/main.py:313`），故 factory 簽名對齊 `(client_, store, question, history)`。

- [ ] **Step 2: Run the new test to verify it passes**

Run: `python -m pytest tests/backend/test_qa_ephemeral_api.py -k feeds_prior_history -v`
Expected: PASS（第二輪 history 含首輪 Q/A）。

- [ ] **Step 3: Run the entire backend suite**

Run: `python -m pytest tests/backend/ -q`
Expected: 全綠，無回歸（特別注意 `test_qa_stream_persist.py` 仍綠——它用 `get_pool` 回 `object()`（truthy pool）+ mock create_session，與 ephemeral 路徑互不干擾）。

- [ ] **Step 4: Confirm no Sentry leak / no DB needed**

Run: `python -m pytest tests/backend/test_qa_ephemeral.py tests/backend/test_qa_ephemeral_api.py -q`
Expected: 全綠且**不需 DATABASE_URL**（純記憶體 + monkeypatch）。conftest.py 已把 `SENTRY_DSN=""` → Sentry 停用。

- [ ] **Step 5: Commit**

```bash
git add tests/backend/test_qa_ephemeral_api.py
git commit -m "test(qa): assert ephemeral multi-turn history feeds into model prompt + full regression

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 11: Playwright 兩 project 設定（有 DB / 無 DB），webServer 帶/不帶 DATABASE_URL

**Files:**
- Modify/Create: `frontend/playwright.config.ts`

- [ ] **Step 1: Inspect or create the config**

先檢查是否已有 config：

Run: `ls frontend/playwright.config.ts 2>/dev/null && echo EXISTS || echo MISSING`

若 MISSING，建立 `frontend/playwright.config.ts`；若 EXISTS，只新增 `qa-with-db` / `qa-no-db` 兩個 project 與對應 webServer。完整檔內容（MISSING 時直接寫入；EXISTS 時對齊既有結構合併 projects 與 webServer 陣列）：

```typescript
// frontend/playwright.config.ts
import { defineConfig, devices } from "@playwright/test";

const BACKEND_NO_DB = "http://127.0.0.1:8001";
const BACKEND_WITH_DB = "http://127.0.0.1:8002";
const FRONTEND = "http://127.0.0.1:3000";

// 有 DB project 需要 DATABASE_URL（本機用 Railway DATABASE_PUBLIC_URL，由環境變數帶入）。
const DB_URL = process.env.DATABASE_URL ?? "";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: [["list"]],
  use: {
    trace: "on-first-retry",
    actionTimeout: 0,
  },
  projects: [
    {
      name: "qa-no-db",
      testMatch: /qa\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        baseURL: FRONTEND,
        // qa.spec.ts 透過 process.env.QA_BACKEND 決定打哪個 backend
      },
      metadata: { backend: BACKEND_NO_DB, hasDb: false },
    },
    {
      name: "qa-with-db",
      testMatch: /qa\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        baseURL: FRONTEND,
      },
      metadata: { backend: BACKEND_WITH_DB, hasDb: true },
    },
  ],
  webServer: [
    {
      // 無 DB backend：刻意不帶 DATABASE_URL → 走 ephemeral
      command:
        "ALLOWED_ORIGIN=* PORT=8001 python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001",
      url: `${BACKEND_NO_DB}/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      cwd: "..",
      env: { DATABASE_URL: "" },  // 明確清空 → 觸發 db.init_pool 早退
    },
    {
      // 有 DB backend：帶 DATABASE_URL（缺省時此 server 仍起，但 with-db 測試會 skip）
      command:
        "ALLOWED_ORIGIN=* PORT=8002 python -m uvicorn backend.main:app --host 127.0.0.1 --port 8002",
      url: `${BACKEND_WITH_DB}/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      cwd: "..",
      env: { DATABASE_URL: DB_URL },
    },
    {
      command: "python -m http.server 3000",
      url: FRONTEND,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
  ],
});
```

> 設計：兩個 backend 同時起在不同 port，project 透過 `metadata.backend` 指向各自 backend。`qa.spec.ts` 從 `testInfo.project.metadata` 取 `backend` 與 `hasDb`。無 DB backend 用 `env.DATABASE_URL=""` 強制 `db.init_pool` 早退（CLAUDE.md 設計決策：未設 → logging disabled）。有 DB backend 需本機帶 `DATABASE_URL`（Railway `DATABASE_PUBLIC_URL`）；缺省時 with-db 測試會 skip（見 Task 12 的 skip gate）。

- [ ] **Step 2: Verify config loads**

Run: `cd frontend && npx playwright test --list`
Expected: 列出 `qa-no-db` 與 `qa-with-db` 兩 project 下的 qa.spec.ts 測試（即使測試本體尚未寫，至少 config 無語法錯）。

- [ ] **Step 3: Commit**

```bash
git add frontend/playwright.config.ts
git commit -m "test(e2e): add qa-with-db / qa-no-db Playwright projects (webServer with/without DATABASE_URL)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 12: Playwright Q&A e2e（Q1–Q5），real backend，兩環境

**Files:**
- Create: `frontend/e2e/qa.spec.ts`

> 紀律（測試策略 §2.10、§6）：禁 mock 自家 `/qa`；role/data-attr locator；web-first 斷言；零 `waitForTimeout`；SSE 用 `expect.poll`/`toPass`；module-state 每測隔離（每測自己開 session）。Gemini 真檢索貴 → 樣本稀少。

- [ ] **Step 1: Write the e2e spec**

先確認前端 Q&A 互動的 selector（subagent 執行時須 Read `frontend/index.html` 與 `frontend/app.js` 找實際 `id`/`data-*`/role，把下方 `data-testid` 佔位換成真實 locator；若前端尚無對應 `data-testid`，在 index.html 補上最小必要的 `data-testid` 屬性——這是允許的前端微調，需附 `?v=N` cache-bust bump 並截圖確認）。建立 `frontend/e2e/qa.spec.ts`：

```typescript
// frontend/e2e/qa.spec.ts
import { test, expect } from "@playwright/test";

// 從 project metadata 取 backend 與是否有 DB。
function ctx(testInfo: any) {
  const m = testInfo.project.metadata ?? {};
  return { backend: m.backend as string, hasDb: m.hasDb as boolean };
}

// 有 DB project 但本機未帶 DATABASE_URL → skip（避免假失敗）。
function skipIfDbExpectedButMissing(testInfo: any) {
  const { hasDb } = ctx(testInfo);
  test.skip(hasDb && !process.env.DATABASE_URL, "DATABASE_URL not set; skip with-db project");
}

test.describe("Q&A 多輪（@critical）", () => {
  test.beforeEach(async ({ page }, testInfo) => {
    skipIfDbExpectedButMissing(testInfo);
    const { backend } = ctx(testInfo);
    // 讓前端打對應 backend：app.js 讀 window.__API_URL__ 覆蓋（subagent 須在 app.js CONFIG.API_URL
    // 加一行 `window.__API_URL__ ?? ...` fallback；若已支援則直接 addInitScript）。
    await page.addInitScript((url) => {
      (window as any).__API_URL__ = url;
    }, backend);
    await page.goto("/index.html");
    // 切到 Q&A 模式
    await page.getByRole("tab", { name: /問答|Q&A|自由提問/ }).click();
  });

  // Q1（with-DB）/ Q2（no-DB）：同一份流程，兩 project 各跑一次。
  test("多輪問答：問→答→追問，不 503，session 與 turn 遞增", async ({ page }, testInfo) => {
    const { backend, hasDb } = ctx(testInfo);

    // 監看 /qa 或 /qa/stream 回應，斷言「絕不 503」。
    const responses: number[] = [];
    page.on("response", (r) => {
      if (r.url().includes("/qa")) responses.push(r.status());
    });

    const input = page.getByRole("textbox", { name: /問題|提問|question/i });
    await input.fill("資料科學要修哪些課？");
    await page.getByRole("button", { name: /送出|提問|問/ }).click();

    // 第一輪答案出現（SSE 串流逐 token → 用 toPass 輪詢非固定延遲）
    const answer = page.getByTestId("qa-answer").first();
    await expect.poll(async () => (await answer.textContent())?.length ?? 0, {
      timeout: 120_000,
    }).toBeGreaterThan(10);

    // markdown 表格 / citations / followup 任一存在（至少 citations 區塊渲染）
    await expect(page.getByTestId("qa-citations").first()).toBeVisible();

    // 追問第二輪：沿用同 session
    await input.fill("那統計呢？");
    await page.getByRole("button", { name: /送出|提問|問/ }).click();
    const secondAnswer = page.getByTestId("qa-answer").nth(1);
    await expect.poll(async () => (await secondAnswer.textContent())?.length ?? 0, {
      timeout: 120_000,
    }).toBeGreaterThan(5);

    // 全程沒有 503
    expect(responses).not.toContain(503);
    expect(responses.length).toBeGreaterThan(0);

    // with-DB：GET /qa/session/{id} 可讀回持久化 turns（≥2）
    if (hasDb) {
      const sid = await page.evaluate(() => (window as any).__lastQaSessionId__);
      expect(sid).toBeTruthy();
      const r = await page.request.get(`${backend}/qa/session/${sid}`);
      expect(r.ok()).toBeTruthy();
      const body = await r.json();
      expect(body.turns.length).toBeGreaterThanOrEqual(2);
    }
  });

  // Q3（no-DB only）：reload 後 ephemeral session 遺失可接受、不崩。
  test("no-DB reload：ephemeral 遺失不崩，可重新提問", async ({ page }, testInfo) => {
    const { hasDb } = ctx(testInfo);
    test.skip(hasDb, "reload-loss only meaningful for no-DB");

    const input = page.getByRole("textbox", { name: /問題|提問|question/i });
    await input.fill("企業管理學什麼？");
    await page.getByRole("button", { name: /送出|提問|問/ }).click();
    await expect.poll(async () =>
      (await page.getByTestId("qa-answer").first().textContent())?.length ?? 0,
      { timeout: 120_000 }).toBeGreaterThan(5);

    await page.reload();
    await page.getByRole("tab", { name: /問答|Q&A|自由提問/ }).click();
    // reload 後重新提問仍正常（不殘留崩潰、無 null 例外）
    const consoleErrors: string[] = [];
    page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
    await input.fill("再問一次：行銷學？");
    await page.getByRole("button", { name: /送出|提問|問/ }).click();
    await expect.poll(async () =>
      (await page.getByTestId("qa-answer").first().textContent())?.length ?? 0,
      { timeout: 120_000 }).toBeGreaterThan(5);
    expect(consoleErrors.join("\n")).not.toMatch(/classList|EventSource|undefined/);
  });

  // Q4（both）：離題引導 + citations 空覆寫防幻覺。
  test("離題問題 → 溫和引導 / 防幻覺（不杜撰課程）", async ({ page }) => {
    const input = page.getByRole("textbox", { name: /問題|提問|question/i });
    await input.fill("今天台北天氣如何？");
    await page.getByRole("button", { name: /送出|提問|問/ }).click();
    const answer = page.getByTestId("qa-answer").first();
    await expect.poll(async () => (await answer.textContent())?.length ?? 0, {
      timeout: 120_000,
    }).toBeGreaterThan(5);
    // 離題：要嘛引導回課程、要嘛誠實查無——不得列出看似真實的課綱連結
    const text = (await answer.textContent()) ?? "";
    expect(text).toMatch(/課程|課綱|問問|查無|協助|推薦|建議/);
  });

  // Q5（both）：markdown XSS 清洗——<script> 不執行。
  test("XSS：問題含 <script> 不被執行，answer 區無 script 標籤", async ({ page }) => {
    let dialogFired = false;
    page.on("dialog", async (d) => { dialogFired = true; await d.dismiss(); });

    const input = page.getByRole("textbox", { name: /問題|提問|question/i });
    await input.fill("<script>window.__xss__=1</script> 政治學如何？");
    await page.getByRole("button", { name: /送出|提問|問/ }).click();
    await expect.poll(async () =>
      (await page.getByTestId("qa-answer").first().textContent())?.length ?? 0,
      { timeout: 120_000 }).toBeGreaterThan(5);

    // DOMPurify 後：DOM 內無 <script>、注入旗標未被設、無 alert dialog
    const scriptInAnswer = await page.locator('[data-testid="qa-answer"] script').count();
    expect(scriptInAnswer).toBe(0);
    const xss = await page.evaluate(() => (window as any).__xss__);
    expect(xss).toBeUndefined();
    expect(dialogFired).toBeFalsy();
  });
});
```

- [ ] **Step 2: Wire frontend hooks the spec depends on**

subagent 須確保下列前端鉤子存在（Read `frontend/app.js` / `frontend/index.html` 後最小增補；屬允許的前端微調，bump `?v=N` 並截圖確認）：
- `window.__API_URL__` 覆蓋 `CONFIG.API_URL`（讓 e2e 指定 backend port）。
- `window.__lastQaSessionId__` 在每次 `/qa` 回應後記下 session_id（with-DB 讀回用）。
- Q&A 區塊有 `data-testid="qa-answer"` / `data-testid="qa-citations"`，模式切換有對應 `role="tab"` 或等價可定位元素。

- [ ] **Step 3: Run no-DB project (cheap, no DB needed)**

Run: `cd frontend && npx playwright test --project=qa-no-db -g "多輪問答|reload|離題|XSS"`
Expected: PASS（Q2/Q3/Q4/Q5 在 no-DB 環境綠；全程無 503）。

- [ ] **Step 4: Run with-DB project (needs DATABASE_URL)**

Run: `cd frontend && DATABASE_URL="$DATABASE_PUBLIC_URL" npx playwright test --project=qa-with-db -g "多輪問答|離題|XSS"`
Expected: PASS（Q1/Q4/Q5 在 with-DB 環境綠；`GET /qa/session/{id}` 讀回 turns ≥2）。未帶 `DATABASE_URL` 則該 project 全 skip（非失敗）。

- [ ] **Step 5: Commit**

```bash
git add frontend/e2e/qa.spec.ts frontend/index.html frontend/app.js
git commit -m "test(e2e): Q&A multi-turn + XSS + anti-hallucination across with-db/no-db (Q1-Q5)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 13: Flake Gate（5x burn-in，Q1/Q2 關鍵流程）

**Files:**
- 無新檔；執行穩定度門檻（測試策略 §6：關鍵 e2e `--repeat-each=5` 全綠才過）

- [ ] **Step 1: Burn-in no-DB 多輪（Q2）**

Run: `cd frontend && npx playwright test --project=qa-no-db -g "多輪問答" --repeat-each=5`
Expected: 5/5 全綠、0 flake。任一失敗 → 用 `superpowers:systematic-debugging` 找根因（常見：SSE 用了固定 sleep、locator 抓到舊輪 answer、session hook 未即時更新），修到 5x 連綠。

- [ ] **Step 2: Burn-in with-DB 多輪 + 讀回（Q1）**

Run: `cd frontend && DATABASE_URL="$DATABASE_PUBLIC_URL" npx playwright test --project=qa-with-db -g "多輪問答" --repeat-each=5`
Expected: 5/5 全綠（含 `GET /qa/session/{id}` 讀回）。未帶 DATABASE_URL → skip（記錄為「環境未驗」，不擋本機 no-DB gate）。

- [ ] **Step 3: Confirm zero flake summary**

確認兩輪 burn-in 輸出皆 `X passed`、無 `flaky`。把結果記入 commit message。

- [ ] **Step 4: Commit (gate evidence)**

```bash
git commit --allow-empty -m "test(e2e): Q1/Q2 5x burn-in zero-flake gate passed

no-db 5/5 green; with-db 5/5 green (or skipped when DATABASE_URL absent)
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage（design §2-§5 + 測試策略 §2.F / §3.3 Q）：**

| Spec 要求 | 落實處 |
|---|---|
| in-memory ephemeral store（module 單例） | Task 1（`_EphemeralStore` + `_STORE` + `reset_ephemeral_store`） |
| `create_session` 無 DB 回 uuid 並註冊 | Task 2（F1） |
| `get_session` 無 DB 查記憶體、查無回 None | Task 3（F2/F4） |
| `insert_turn` 無 DB 寫記憶體 | Task 4（F3 寫入） |
| `bump_session`/`get_session_turns` 無 DB 走記憶體 + 多輪重建 | Task 5（F3 完整） |
| session 隔離 | Task 6（F5） |
| `build_history_from_turns` 空輸入 | Task 6（F7） |
| DB 在時行為完全不變（回歸保護） | Task 7（F6，fake pool 斷言走 SQL、store 未觸碰） |
| main.py 移除 `POST /qa` 503 死路徑 | Task 8 |
| main.py 移除 `/qa/stream` SSE-error 死路徑 | Task 9 |
| session 解析相容 in-memory（多輪 history 串接 prompt） | Task 10（feeds_prior_history 斷言） |
| 「session 後端」抽象、main.py 不判斷 pool | Task 1-9（抽象收斂在 qa_logger，main.py 僅移除 503 分支，呼叫端不判斷 pool） |
| store teardown 每測隔離 | Task 1（`reset_ephemeral_store` + autouse fixture） |
| e2e 兩 project 切有 DB/無 DB（webServer 帶/不帶 DATABASE_URL） | Task 11 |
| e2e Q1–Q5（多輪讀回 / no-DB 不 503 / reload / 離題防幻覺 / XSS） | Task 12 |
| 禁 mock 自家 /qa；real backend | Task 12（只 mock 第三方/前端 hook，不 mock /qa） |
| 5x flake gate（Q1/Q2） | Task 13 |
| 降級邊界：ephemeral 重啟遺失、`/qa/session/{id}` 對 ephemeral 回空可接受 | Task 12 Q3（reload 不崩）；`/qa/session/{id}` 未改 → 無 DB 走 `get_session_turns(None,..)` 回 store turns（同進程內可回，跨重啟空）——符合「可接受」邊界 |

無 spec 缺口。

**2. Placeholder scan：** 每個 code step 皆含完整可執行碼，無 TBD/TODO/「類似 Task N」。唯二「無新實作」的 Task 6/7 已明示是回歸鎖（行為由前序 task 實作），非 placeholder。Task 11/12 對前端 selector 的不確定性以「subagent Read 後對齊真實 locator」明確指示，並標明允許的前端微調範圍（加 `data-testid` + `?v=N` bump），非含糊。

**3. Type consistency：**
- store 方法名一致：`create()` / `get()` / `add_turn()` / `bump()` / `turns()` 跨 Task 1-7 一致。
- `reset_ephemeral_store` / `_STORE` 命名跨 Task 1/6/7/8/9 一致。
- 五個 session 函式 signature 全程維持 `(pool, ...)`，回傳型別與原始一致（`create_session→Optional[str]`、`get_session→Optional[dict]`、`insert_turn→Optional[int]`、`bump_session→None`、`get_session_turns→list[dict]`）。
- in-memory turn dict 形狀含 `question`/`answer`/`success`，與 `build_history_from_turns`（讀 `answer`/`success`/`question`）與 DB row 一致 → F3/F7 不需改純函式。
- `stream_answer(client, store, question, history)` 簽名與 main.py:313 一致（Task 10 factory 對齊）。

一致，無 signature 漂移。

---

**Plan complete and saved to `docs/superpowers/plans/2026-06-04-qa-graceful-degradation-plan.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — 每 task 派新 subagent、task 間兩段審查、快速迭代。

**2. Inline Execution** — 本 session 用 executing-plans、批次執行加檢查點。

**Which approach?**
