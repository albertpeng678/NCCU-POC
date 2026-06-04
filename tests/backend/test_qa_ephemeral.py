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
