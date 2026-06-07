# tests/backend/test_recommend_stream_budget.py
"""GET /recommend/stream 命中 career_budget → 瞬間 5 階段 + result（0 次即時 AI）；未命中 → 即時串流。"""
import json
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

from backend.main import app

_BUDGET = {
    "courses": [{"course_id": "070415001", "name": "資料科學基礎", "group": "core", "rank": 1}],
    "pool_size": 1, "built_at": "2026-06-08",
}


def _events(text):
    out, cur = [], None
    for line in text.splitlines():
        if line.startswith("event:"):
            cur = line[len("event:"):].strip()
        elif line.startswith("data:"):
            out.append((cur, line[len("data:"):].strip()))
    return out


def _boom_stream(*a, **k):
    raise AssertionError("命中時不該呼叫即時 stream_recommendation")


def test_stream_hit_emits_5_stages_and_result_without_ai():
    with patch("backend.main.get_budget", AsyncMock(return_value=_BUDGET)), \
         patch("backend.main.get_pool", return_value=MagicMock()), \
         patch("backend.main.stream_recommendation", _boom_stream), \
         patch("backend.main._spawn_bg", MagicMock()), \
         patch("backend.main._background_log_and_judge", MagicMock()):
        resp = TestClient(app).get("/recommend/stream?career=資料科學家")
    evs = _events(resp.text)
    stages = [json.loads(d)["key"] for e, d in evs if e == "stage"]
    # 5 階段各 start+done = 10 個 stage 事件
    assert stages.count("understand") == 2 and stages.count("finalize") == 2
    results = [json.loads(d) for e, d in evs if e == "result"]
    assert len(results) == 1
    assert [c["course_id"] for c in results[0]["courses"]] == ["070415001"]


def test_stream_miss_uses_live_stream():
    async def _live(*a, **k):
        yield {"event": "result", "data": {"career": "資料科學家", "courses": [{"course_id": "y", "name": "課"}],
                                            "batch_size": 10, "latency_ms": 7, "seed": 1}}
    called = {"n": 0}
    def _spy(*a, **k):
        called["n"] += 1
        return _live()
    with patch("backend.main.get_budget", AsyncMock(return_value=None)), \
         patch("backend.main.get_pool", return_value=MagicMock()), \
         patch("backend.main.stream_recommendation", _spy), \
         patch("backend.main._spawn_bg", MagicMock()), \
         patch("backend.main._background_log_and_judge", MagicMock()):
        resp = TestClient(app).get("/recommend/stream?career=資料科學家")
    results = [json.loads(d) for e, d in _events(resp.text) if e == "result"]
    assert called["n"] == 1
    assert [c["course_id"] for c in results[0]["courses"]] == ["y"]
