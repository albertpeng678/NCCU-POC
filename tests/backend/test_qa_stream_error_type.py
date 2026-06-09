# tests/backend/test_qa_stream_error_type.py
"""/qa/stream：error 事件帶語意 error_type（rate_limited/timeout/unknown）、done 事件帶 no_match。
讓前端走差異化分支（暫時性過載→重試 vs 查無資料→換個問法）。
"""
import json
import httpx
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from openai import APIStatusError as _OAIStatus
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


def _boom(exc):
    async def _gen(*a, **k):
        raise exc
        yield  # pragma: no cover
    return _gen


def _err_type(client, exc):
    with patch("backend.main._QA_MODE", "stream"), \
         patch("backend.main.get_pool", return_value=None), \
         patch("backend.main.stream_answer", _boom(exc)), \
         patch("backend.main.load_courses_meta", return_value={}):
        resp = client.get("/qa/stream?question=測試")
    evs = _events(resp.text)
    err = [json.loads(d) for e, d in evs if e == "error"]
    assert err, f"無 error 事件：{resp.text[:200]}"
    return err[0]["error_type"]


def test_stream_error_503_is_rate_limited(client):
    exc = _OAIStatus(
        "overloaded",
        response=httpx.Response(503, request=httpx.Request("GET", "http://test.example.com")),
        body=None,
    )
    assert _err_type(client, exc) == "rate_limited"


def test_stream_error_generic_is_unknown(client):
    assert _err_type(client, ValueError("boom")) == "unknown"


def test_stream_done_carries_no_match_true_on_override(client):
    # grounding 空 + 答案像列課程（9 碼代號）→ finalize 防幻覺覆寫 → done.no_match=True
    async def _fake(*a, **k):
        yield {"event": "done", "data": {"course_ids": [], "answer_text": "課號 999999999 是好課。"}}
    with patch("backend.main._QA_MODE", "stream"), \
         patch("backend.main.get_pool", return_value=None), \
         patch("backend.main.stream_answer", _fake), \
         patch("backend.main.load_courses_meta", return_value={}):
        resp = client.get("/qa/stream?question=有沒有教戀愛的課")
    done = [json.loads(d) for e, d in _events(resp.text) if e == "done"][0]
    assert done["no_match"] is True


def test_stream_done_no_match_false_on_normal_answer(client):
    async def _fake(*a, **k):
        yield {"event": "done", "data": {"course_ids": [], "answer_text": "政治學是一門探討權力的課。"}}
    with patch("backend.main._QA_MODE", "stream"), \
         patch("backend.main.get_pool", return_value=None), \
         patch("backend.main.stream_answer", _fake), \
         patch("backend.main.load_courses_meta", return_value={}):
        resp = client.get("/qa/stream?question=政治學在學什麼")
    done = [json.loads(d) for e, d in _events(resp.text) if e == "done"][0]
    assert done["no_match"] is False
