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
