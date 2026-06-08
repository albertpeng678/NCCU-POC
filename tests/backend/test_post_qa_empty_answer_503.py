# tests/backend/test_post_qa_empty_answer_503.py
"""POST /qa：stream35 模式下空答案（TOO_MANY_TOOL_CALLS）→ 回 503，不落空白成功 turn。
鏡像 test_qa_mode_stream35.py POST 結構，mock get_pool=None（無 DB）。
"""
from unittest.mock import patch
from fastapi.testclient import TestClient

from backend import qa_logger


def _autoclean(fn):
    """每個測試前後重設 ephemeral store（避免 turn 洩漏污染）。"""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        qa_logger.reset_ephemeral_store()
        try:
            return fn(*args, **kwargs)
        finally:
            qa_logger.reset_ephemeral_store()

    return wrapper


async def _fake_stream_empty(_client, _store, _question, _history=None, **_kw):
    """模擬 TOO_MANY_TOOL_CALLS → stream_answer_structured 回空 answer。"""
    yield {
        "event": "done",
        "data": {
            "course_ids": [],
            "answer_text": '{"answer":"","followup_suggestions":[]}',
        },
    }


@_autoclean
def test_post_qa_empty_answer_returns_503():
    """空答案 → POST /qa 回 503（不回 200 空白）。"""
    with patch("backend.main._QA_MODE", "stream35"), \
         patch("backend.main.get_pool", return_value=None), \
         patch("backend.main.stream_answer_structured", _fake_stream_empty), \
         patch("backend.main.load_courses_meta", return_value={}):
        import backend.main as m
        resp = TestClient(m.app).post("/qa", json={"question": "廣泛查詢測試"})

    assert resp.status_code == 503, f"空答案應回 503，實際: {resp.status_code} {resp.text}"
    assert "incomplete" in resp.text.lower(), f"detail 應含 'incomplete'，實際: {resp.text}"
