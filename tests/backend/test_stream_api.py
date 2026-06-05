# tests/backend/test_stream_api.py
"""SSE 端點整合測試：用 TestClient 收 SSE 文字流，斷言事件協定。
mock backend.main 內的串流產生器（不打真 Gemini）。"""
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient


async def _gen_recommend_ok():
    yield {"event": "stage", "data": {"n": 1, "key": "understand", "status": "start"}}
    yield {"event": "stage", "data": {"n": 1, "key": "understand", "status": "done"}}
    yield {"event": "result", "data": {"career": "產品經理(PM)", "courses": [],
           "batch_size": 10, "latency_ms": 0, "seed": 7}}


async def _gen_recommend_no_match():
    yield {"event": "stage", "data": {"n": 1, "key": "understand", "status": "start"}}
    yield {"event": "no_match", "data": {"career": "asdf", "message": "查無"}}


@pytest.fixture
def client():
    from backend.main import app
    return TestClient(app)


def _events(text):
    """把 SSE 原始文字切成 (event, data) tuples。"""
    out = []
    cur_event = None
    for line in text.splitlines():
        if line.startswith("event:"):
            cur_event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            out.append((cur_event, line[len("data:"):].strip()))
    return out


def test_recommend_stream_emits_stages_and_result(client):
    with patch("backend.main.stream_recommendation", return_value=_gen_recommend_ok()):
        resp = client.get("/recommend/stream?career=產品經理(PM)&seed=7")
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    evs = _events(resp.text)
    names = [e for e, _ in evs]
    assert "stage" in names
    assert "result" in names
    assert '"seed": 7' in resp.text or '"seed":7' in resp.text


def test_recommend_stream_no_match(client):
    with patch("backend.main.stream_recommendation", return_value=_gen_recommend_no_match()):
        resp = client.get("/recommend/stream?career=asdf")
    assert resp.status_code == 200
    evs = _events(resp.text)
    assert any(e == "no_match" for e, _ in evs)


async def _gen_qa_ok():
    yield {"event": "token", "data": {"text": "政治學"}}
    yield {"event": "token", "data": {"text": " 不錯"}}
    yield {"event": "done", "data": {"course_ids": ["000211012"],
                                     "answer_text": "政治學 **不錯**"}}


def test_qa_stream_tokens_then_done(client):
    meta = {"000211012": {"name": "政治學", "department": "政治系", "teacher": "蔡",
                          "credits": 3.0, "syllabus_url": "https://x/a"}}
    with patch("backend.main._QA_MODE", "stream"), \
         patch("backend.main.stream_answer", return_value=_gen_qa_ok()), \
         patch("backend.main.get_pool", return_value=object()), \
         patch("backend.main.create_session", new=AsyncMock(return_value="sess-1")), \
         patch("backend.main.get_session_turns", new=AsyncMock(return_value=[])), \
         patch("backend.main.insert_turn", new=AsyncMock(return_value=1)), \
         patch("backend.main.bump_session", new=AsyncMock(return_value=None)), \
         patch("backend.main.load_courses_meta", return_value=meta):
        resp = client.get("/qa/stream?question=問政治學")
    assert resp.status_code == 200
    evs = _events(resp.text)
    names = [e for e, _ in evs]
    assert names.count("token") == 2
    assert "done" in names
    done_data = [d for e, d in evs if e == "done"][0]
    # 契約：done 用 followup_suggestions / turn_number / session_id / citations
    assert "政治學" in done_data
    assert "sess-1" in done_data
    assert "turn_number" in done_data
    assert "followup_suggestions" in done_data


def test_qa_stream_hallucination_override(client):
    """citations 空但答案像列課程（含表格） → 覆寫 NO_RESULTS_MESSAGE。"""
    async def _gen_no_cite():
        yield {"event": "token", "data": {"text": "| 課程 | 系所 |\n| --- | --- |\n"}}
        yield {"event": "done", "data": {"course_ids": [],
               "answer_text": "| 課程 | 系所 |\n| --- | --- |\n| 假課 | 假系 |"}}

    with patch("backend.main._QA_MODE", "stream"), \
         patch("backend.main.stream_answer", return_value=_gen_no_cite()), \
         patch("backend.main.get_pool", return_value=object()), \
         patch("backend.main.create_session", new=AsyncMock(return_value="sess-2")), \
         patch("backend.main.get_session_turns", new=AsyncMock(return_value=[])), \
         patch("backend.main.insert_turn", new=AsyncMock(return_value=1)), \
         patch("backend.main.bump_session", new=AsyncMock(return_value=None)), \
         patch("backend.main.load_courses_meta", return_value={}):
        resp = client.get("/qa/stream?question=亂問")
    evs = _events(resp.text)
    done_data = [d for e, d in evs if e == "done"][0]
    assert "沒有找到" in done_data  # NO_RESULTS_MESSAGE 片段
