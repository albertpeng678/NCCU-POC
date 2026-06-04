# tests/backend/test_recommend_stream_logging.py
"""F5：recommend_stream 串流路徑補 logging/judge。
串流成功（收到 result 事件）→ fire-and-forget _background_log_and_judge → insert_log 被呼叫。
mock insert_log/evaluate_recommendation/update_judge_scores，不打真 DB/Gemini。"""
import asyncio
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from backend.main import app
    return TestClient(app)


async def _gen_ok():
    yield {"event": "stage", "data": {"n": 1, "key": "understand", "status": "done"}}
    yield {"event": "result", "data": {
        "career": "產品經理(PM)",
        "groups": {"core": [{"course_id": "a"}, {"course_id": "b"}],
                   "supporting": [{"course_id": "c"}], "extended": []},
        "latency_ms": 0, "seed": 7,
    }}


async def _gen_no_match():
    yield {"event": "stage", "data": {"n": 1, "key": "understand", "status": "start"}}
    yield {"event": "no_match", "data": {"career": "asdf", "message": "查無"}}


def test_recommend_stream_logs_on_success(client):
    """成功串流 → insert_log 被呼叫一次，且帶 result + 推估的 stage1_count。"""
    insert_mock = AsyncMock(return_value=42)
    with patch("backend.main.stream_recommendation", return_value=_gen_ok()), \
         patch("backend.main.get_pool", return_value=object()), \
         patch("backend.main.insert_log", new=insert_mock), \
         patch("backend.main.evaluate_recommendation", new=AsyncMock(return_value=None)), \
         patch("backend.main.update_judge_scores", new=AsyncMock(return_value=None)):
        resp = client.get("/recommend/stream?career=產品經理(PM)&seed=7")
    assert resp.status_code == 200
    insert_mock.assert_awaited_once()
    # build_log_record(career, result, stage1_count, error) 寫進 record；
    # 只要被呼叫即代表串流路徑不再斷裂 logging。
    record = insert_mock.await_args.args[1]
    assert record.get("career") == "產品經理(PM)"


def test_recommend_stream_no_match_does_not_log_success(client):
    """no_match（無 result 事件）→ 不應寫成功 log（result=None）。"""
    insert_mock = AsyncMock(return_value=None)
    with patch("backend.main.stream_recommendation", return_value=_gen_no_match()), \
         patch("backend.main.get_pool", return_value=object()), \
         patch("backend.main.insert_log", new=insert_mock), \
         patch("backend.main.evaluate_recommendation", new=AsyncMock(return_value=None)), \
         patch("backend.main.update_judge_scores", new=AsyncMock(return_value=None)):
        resp = client.get("/recommend/stream?career=asdf")
    assert resp.status_code == 200
    # 無 result → 不觸發背景 logging（避免寫一筆 result=None 的假成功 log）
    insert_mock.assert_not_awaited()


def test_recommend_stream_triggers_judge_on_success(client):
    """有 result 且 insert_log 回 id → evaluate_recommendation 被呼叫（judge 不再缺席）。"""
    judge_mock = AsyncMock(return_value=None)
    with patch("backend.main.stream_recommendation", return_value=_gen_ok()), \
         patch("backend.main.get_pool", return_value=object()), \
         patch("backend.main.insert_log", new=AsyncMock(return_value=99)), \
         patch("backend.main.evaluate_recommendation", new=judge_mock), \
         patch("backend.main.update_judge_scores", new=AsyncMock(return_value=None)):
        resp = client.get("/recommend/stream?career=產品經理(PM)&seed=7")
    assert resp.status_code == 200
    judge_mock.assert_awaited_once()
