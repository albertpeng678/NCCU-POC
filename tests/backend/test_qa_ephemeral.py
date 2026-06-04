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
