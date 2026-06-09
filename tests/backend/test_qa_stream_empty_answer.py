# tests/backend/test_qa_stream_empty_answer.py
"""/qa/stream：空答案 → 送 error(incomplete)，不送 done。"""
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


async def _fake_stream_empty_answer(_client, _store, _question, _history=None, **_kw):
    """模擬空答案 → stream_answer 回空 answer_text。"""
    yield {
        "event": "done",
        "data": {
            "course_ids": [],
            "answer_text": "",
        },
    }


def test_stream_empty_answer_yields_error_not_done(client):
    """空答案 → 回應含 event: error 且 error_type=incomplete，不含 event: done。"""
    with patch("backend.main._QA_MODE", "stream"), \
         patch("backend.main.get_pool", return_value=None), \
         patch("backend.main.stream_answer", _fake_stream_empty_answer), \
         patch("backend.main.load_courses_meta", return_value={}):
        resp = client.get("/qa/stream?question=廣泛查詢測試")

    evs = _events(resp.text)
    event_names = [e for e, _ in evs]

    # 必須有 error 事件
    error_events = [json.loads(d) for e, d in evs if e == "error"]
    assert error_events, f"應有 error 事件，實際事件: {event_names}"

    # error_type 必須是 incomplete
    assert error_events[0]["error_type"] == "incomplete", \
        f"error_type 應為 incomplete，實際: {error_events[0]}"

    # 不能有 done 事件
    assert "done" not in event_names, \
        f"空答案不應送出 done 事件，實際事件: {event_names}"
