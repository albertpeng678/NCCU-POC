# tests/backend/test_qa_stream_persist.py
"""F3：qa_stream 斷線/半截 turn 污染防護。
- 斷線（mid-stream，無 done）→ 不寫半截 turn（不呼叫 insert_turn / bump_session）。
- 連續兩輪 turn_number 遞增不重複（turn_count 由 bump_session 推進）。
- 多輪歷史過濾掉 success=false / answer 空的失敗輪（不污染上下文）。
"""
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from backend.qa_logger import build_history_from_turns


@pytest.fixture
def client():
    from backend.main import app
    return TestClient(app)


def _events(text):
    out = []
    cur = None
    for line in text.splitlines():
        if line.startswith("event:"):
            cur = line[len("event:"):].strip()
        elif line.startswith("data:"):
            out.append((cur, line[len("data:"):].strip()))
    return out


# ---------- 歷史過濾（純函式）----------

def test_build_history_filters_failed_turns():
    turns = [
        {"question": "Q1", "answer": "A1", "success": True},
        {"question": "Q2", "answer": None, "success": False},     # 斷線半截輪
        {"question": "Q3", "answer": "", "success": True},        # 空答案
        {"question": "Q4", "answer": "A4", "success": True},
    ]
    hist = build_history_from_turns(turns)
    assert hist == [
        {"question": "Q1", "answer": "A1"},
        {"question": "Q4", "answer": "A4"},
    ]


def test_build_history_filters_when_success_column_missing():
    # success 欄缺省時，以「answer 非空」為準
    turns = [
        {"question": "Q1", "answer": "A1"},
        {"question": "Q2", "answer": None},
        {"question": "Q3", "answer": "  "},
    ]
    hist = build_history_from_turns(turns)
    assert hist == [{"question": "Q1", "answer": "A1"}]


# ---------- 斷線：不寫半截 turn ----------

def test_qa_stream_disconnect_does_not_persist_half_turn(client):
    """斷線（is_disconnected True）mid-stream → 無 done → 不呼叫 insert_turn / bump_session。"""
    async def _gen_tokens_only():
        yield {"event": "token", "data": {"text": "政治學"}}
        yield {"event": "token", "data": {"text": " 不錯"}}
        # 斷線：永遠沒走到 done

    insert_mock = AsyncMock(return_value=1)
    bump_mock = AsyncMock(return_value=None)
    with patch("backend.main._QA_MODE", "stream"), \
         patch("backend.main.stream_answer", return_value=_gen_tokens_only()), \
         patch("backend.main.get_pool", return_value=object()), \
         patch("backend.main.create_session", new=AsyncMock(return_value="sess-x")), \
         patch("backend.main.get_session_turns", new=AsyncMock(return_value=[])), \
         patch("backend.main.insert_turn", new=insert_mock), \
         patch("backend.main.bump_session", new=bump_mock), \
         patch("backend.main.load_courses_meta", return_value={}), \
         patch("starlette.requests.Request.is_disconnected",
               new=AsyncMock(return_value=True)):
        resp = client.get("/qa/stream?question=問政治學")
    assert resp.status_code == 200
    # 斷線時不落 DB → 不污染 turn 計數/歷史
    insert_mock.assert_not_awaited()
    bump_mock.assert_not_awaited()


# ---------- 成功輪：寫 turn 並 bump（turn_number 遞增不重複）----------

def test_qa_stream_success_persists_and_bumps(client):
    """成功完成（有 done）→ insert_turn + bump_session 各一次（推進 turn_count）。"""
    async def _gen_ok():
        yield {"event": "token", "data": {"text": "政治學"}}
        yield {"event": "done", "data": {"course_ids": ["000211012"],
                                         "answer_text": "政治學 **不錯**"}}

    meta = {"000211012": {"name": "政治學", "department": "政治系", "teacher": "蔡",
                          "credits": 3.0, "syllabus_url": "https://x/a"}}
    insert_mock = AsyncMock(return_value=1)
    bump_mock = AsyncMock(return_value=None)
    with patch("backend.main._QA_MODE", "stream"), \
         patch("backend.main.stream_answer", return_value=_gen_ok()), \
         patch("backend.main.get_pool", return_value=object()), \
         patch("backend.main.create_session", new=AsyncMock(return_value="sess-y")), \
         patch("backend.main.get_session_turns", new=AsyncMock(return_value=[])), \
         patch("backend.main.insert_turn", new=insert_mock), \
         patch("backend.main.bump_session", new=bump_mock), \
         patch("backend.main.load_courses_meta", return_value=meta):
        resp = client.get("/qa/stream?question=問政治學")
    assert resp.status_code == 200
    insert_mock.assert_awaited_once()
    bump_mock.assert_awaited_once()
    # turn_number 來自 (turn_count or 0)+1；第一輪 turn_count=0 → turn_number=1
    assert insert_mock.await_args.args[2] == 1


def test_qa_stream_second_turn_number_increments(client):
    """既有 session 已有 1 輪（turn_count=1）→ 下一輪 turn_number=2，不重複。"""
    async def _gen_ok():
        yield {"event": "done", "data": {"course_ids": [], "answer_text": "答案"}}

    insert_mock = AsyncMock(return_value=2)
    with patch("backend.main._QA_MODE", "stream"), \
         patch("backend.main.stream_answer", return_value=_gen_ok()), \
         patch("backend.main.get_pool", return_value=object()), \
         patch("backend.main.get_session",
               new=AsyncMock(return_value={"turn_count": 1, "last_interaction_id": ""})), \
         patch("backend.main.get_session_turns", new=AsyncMock(return_value=[
               {"question": "Q1", "answer": "A1", "success": True}])), \
         patch("backend.main.insert_turn", new=insert_mock), \
         patch("backend.main.bump_session", new=AsyncMock(return_value=None)), \
         patch("backend.main.load_courses_meta", return_value={}):
        resp = client.get("/qa/stream?question=第二問&session_id=11111111-1111-1111-1111-111111111111")
    assert resp.status_code == 200
    evs = _events(resp.text)
    done_data = [d for e, d in evs if e == "done"][0]
    assert '"turn_number": 2' in done_data or '"turn_number":2' in done_data
    assert insert_mock.await_args.args[2] == 2
