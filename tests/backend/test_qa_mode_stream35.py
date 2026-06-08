# tests/backend/test_qa_mode_stream35.py
"""POST /qa 在 QA_MODE=stream35（預設）下，應選 stream_answer_structured 生成器，
不得選 stream_answer（2.5-flash history-based 舊路徑）。
"""
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from backend import qa_logger


@pytest.fixture(autouse=True)
def _clean_store():
    qa_logger.reset_ephemeral_store()
    yield
    qa_logger.reset_ephemeral_store()


async def _fake_stream_structured(_client, _store, _question, _history=None, **_kw):
    """模擬 stream_answer_structured：yield 一個含合法 JSON answer_text 的 done 事件。"""
    yield {
        "event": "done",
        "data": {
            "course_ids": [],
            "answer_text": '{"answer":"嗨","followup_suggestions":[]}',
        },
    }


async def _fake_stream_plain(_client, _store, _question, _history=None, **_kw):
    """模擬 stream_answer（舊路徑）——不應被呼叫。"""
    yield {
        "event": "done",
        "data": {
            "course_ids": [],
            "answer_text": '{"answer":"舊路徑","followup_suggestions":[]}',
        },
    }


def test_post_qa_stream35_uses_structured_generator():
    """QA_MODE=stream35 → POST /qa 走 stream_answer_structured，不走 stream_answer。"""
    used = {"structured": False, "plain": False}

    async def _structured_spy(_client, _store, _question, _history=None, **_kw):
        used["structured"] = True
        async for ev in _fake_stream_structured(_client, _store, _question, _history):
            yield ev

    async def _plain_spy(_client, _store, _question, _history=None, **_kw):
        used["plain"] = True
        async for ev in _fake_stream_plain(_client, _store, _question, _history):
            yield ev

    meta = {}
    with patch("backend.main._QA_MODE", "stream35"), \
         patch("backend.main.get_pool", return_value=None), \
         patch("backend.main.stream_answer_structured", _structured_spy), \
         patch("backend.main.stream_answer", _plain_spy), \
         patch("backend.main.load_courses_meta", return_value=meta):
        import backend.main as m
        resp = TestClient(m.app).post("/qa", json={"question": "測試"})

    assert resp.status_code == 200, resp.text
    assert used["structured"] is True, "stream_answer_structured 應被呼叫"
    assert used["plain"] is False, "stream_answer（舊路徑）不應被呼叫"
