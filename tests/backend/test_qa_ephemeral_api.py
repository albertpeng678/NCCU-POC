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
    with patch("backend.main._QA_MODE", "stream"), \
         patch("backend.main.get_pool", return_value=None), \
         patch("backend.main.answer_question", return_value=fake_result), \
         patch("backend.main.load_courses_meta", return_value={}), \
         patch("backend.qa.extract_citations_by_name", return_value=[]):
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
    with patch("backend.main._QA_MODE", "stream"), \
         patch("backend.main.get_pool", return_value=None), \
         patch("backend.main.answer_question", return_value=fake_result), \
         patch("backend.main.load_courses_meta", return_value={}), \
         patch("backend.qa.extract_citations_by_name", return_value=[]):
        r1 = client.post("/qa", json={"question": "Q1"})
        sid = r1.json()["session_id"]
        r2 = client.post("/qa", json={"question": "Q2", "session_id": sid})
    assert r2.status_code == 200, r2.text
    assert r2.json()["session_id"] == sid
    assert r2.json()["turn_number"] == 2


# ---------- GET /qa/stream：無 DB → 不發 ServiceUnavailable error 事件，正常 done ----------

def test_qa_stream_no_db_does_not_emit_service_unavailable(client):
    async def _gen_ok():
        yield {"event": "token", "data": {"text": "政治學"}}
        yield {"event": "done", "data": {"course_ids": [], "answer_text": "政治學 **不錯**"}}

    with patch("backend.main._QA_MODE", "stream"), \
         patch("backend.main.get_pool", return_value=None), \
         patch("backend.main.stream_answer", return_value=_gen_ok()), \
         patch("backend.main.load_courses_meta", return_value={}), \
         patch("backend.qa.extract_citations_by_name", return_value=[]):
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

    with patch("backend.main._QA_MODE", "stream"), \
         patch("backend.main.get_pool", return_value=None), \
         patch("backend.main.stream_answer", side_effect=lambda *a, **k: _gen()), \
         patch("backend.main.load_courses_meta", return_value={}), \
         patch("backend.qa.extract_citations_by_name", return_value=[]):
        r1 = client.get("/qa/stream?question=Q1")
        sid = [json.loads(d) for e, d in _events(r1.text) if e == "done"][0]["session_id"]
        r2 = client.get(f"/qa/stream?question=Q2&session_id={sid}")
    done2 = [json.loads(d) for e, d in _events(r2.text) if e == "done"][0]
    assert done2["session_id"] == sid
    assert done2["turn_number"] == 2


# ---------- Task 10: ephemeral 多輪歷史串接 ----------

def test_qa_stream_no_db_feeds_prior_history_to_model(client):
    """第二輪：stream_answer 收到的 history 應含第一輪 (Q1,A1)，證明 ephemeral 上下文有串進 prompt。"""
    captured = {}

    def _stream_factory(client_, store, question, history):
        captured["history"] = history
        async def g():
            yield {"event": "done", "data": {"course_ids": [], "answer_text": "回答"}}
        return g()

    with patch("backend.main._QA_MODE", "stream"), \
         patch("backend.main.get_pool", return_value=None), \
         patch("backend.main.stream_answer", side_effect=_stream_factory), \
         patch("backend.main.load_courses_meta", return_value={}), \
         patch("backend.qa.extract_citations_by_name", return_value=[]):
        r1 = client.get("/qa/stream?question=Q1")
        sid = [json.loads(d) for e, d in _events(r1.text) if e == "done"][0]["session_id"]
        captured.clear()
        client.get(f"/qa/stream?question=Q2&session_id={sid}")

    assert captured["history"] == [{"question": "Q1", "answer": "回答"}]
