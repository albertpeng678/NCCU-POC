# tests/backend/test_qa_post_history.py
"""修『Invalid previous_interaction_id』：stream 模式的 POST /qa（fallback）必須走 history-based
stream_answer（drain 成完整答案），不得呼叫 interactions API（answer_question + previous_interaction_id）。
根因：串流回合把 last_interaction_id 存成 ""，fallback 傳空字串給 interactions → 400。
"""
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from backend import qa_logger
from backend.qa import NO_RESULTS_MESSAGE


@pytest.fixture(autouse=True)
def _clean_store():
    qa_logger.reset_ephemeral_store()
    yield
    qa_logger.reset_ephemeral_store()


@pytest.fixture
def client():
    from backend.main import app
    return TestClient(app)


async def _fake_stream(_client, _store, _question, _history=None):
    yield {"event": "token", "data": {"text": "政治學 **不錯**"}}
    yield {"event": "done", "data": {
        "course_ids": ["000211012"],
        "answer_text": '{"answer": "政治學 **不錯**", "followup_suggestions": ["要不要看國際關係？"]}',
    }}


def _boom_interactions(*a, **k):
    raise AssertionError("stream 模式 POST /qa 不該呼叫 interactions API（answer_question）")


def test_post_qa_stream_mode_uses_history_not_interactions(client):
    """stream 模式 POST /qa：走 stream_answer（history），絕不呼叫 answer_question（interactions）。"""
    meta = {"000211012": {"name": "政治學", "department": "政治系", "teacher": "x",
                          "credits": 3.0, "syllabus_url": "#"}}
    with patch("backend.main._QA_MODE", "stream"), \
         patch("backend.main.get_pool", return_value=None), \
         patch("backend.main.answer_question", side_effect=_boom_interactions), \
         patch("backend.main.stream_answer", _fake_stream), \
         patch("backend.main.load_courses_meta", return_value=meta):
        resp = client.post("/qa", json={"question": "問政治學"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "政治學" in body["answer"]
    assert body["session_id"]
    assert body["turn_number"] == 1


async def _fake_stream_hallucination(_client, _store, _question, _history=None):
    """空 grounding（course_ids=[]）但答案像在列課程（markdown 表格 + 9碼課號）→ 應觸發防幻覺覆寫。"""
    table = ("| 課程名稱 | 系所 | 重點 |\n| --- | --- | --- |\n"
             "| 不存在的鬼課 000211012 | 鬼系 | 純屬虛構 |")
    yield {"event": "done", "data": {"course_ids": [], "answer_text": table}}


def test_post_qa_persists_finalized_answer_not_prefinalize(client):
    """空 citations + 答案像列課程 → finalize 防幻覺覆寫；持久化與回傳都應是覆寫後答案。
    finalize 必須先於 insert_turn，否則幻覺答案會進 qa_turn → build_history 餵回下一輪污染上下文
    （與 /qa/stream 持久化 finalize 後答案的行為對齊）。"""
    captured = {}

    async def _capture_insert(pool, sid, tn, q, result, error):
        # 快照 call-time 的字串值（非 dict 參照）——真實 insert_turn 在此刻就把答案寫進 DB；
        # 若只存參照，finalize 之後的就地覆寫會遮蔽 bug。
        captured["answer"] = result["answer"] if result else None
        return "turn-1"

    with patch("backend.main._QA_MODE", "stream"), \
         patch("backend.main.get_pool", return_value=None), \
         patch("backend.main.stream_answer", _fake_stream_hallucination), \
         patch("backend.main.load_courses_meta", return_value={}), \
         patch("backend.main.insert_turn", side_effect=_capture_insert):
        resp = client.post("/qa", json={"question": "列課給我"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["answer"] == NO_RESULTS_MESSAGE   # 回傳已覆寫
    assert captured["answer"] == NO_RESULTS_MESSAGE      # 持久化也存覆寫後答案（核心斷言）


def test_post_qa_stream_mode_multi_turn_no_invalid_prev_id(client):
    """第二輪（session 的 last_interaction_id 為 ""）也不該把空 prev id 傳給 interactions。"""
    meta = {"000211012": {"name": "政治學", "department": "政治系", "teacher": "x",
                          "credits": 3.0, "syllabus_url": "#"}}
    with patch("backend.main._QA_MODE", "stream"), \
         patch("backend.main.get_pool", return_value=None), \
         patch("backend.main.answer_question", side_effect=_boom_interactions), \
         patch("backend.main.stream_answer", _fake_stream), \
         patch("backend.main.load_courses_meta", return_value=meta):
        r1 = client.post("/qa", json={"question": "第一輪"})
        sid = r1.json()["session_id"]
        r2 = client.post("/qa", json={"question": "第二輪", "session_id": sid})
    assert r2.status_code == 200, r2.text
    assert r2.json()["turn_number"] == 2
