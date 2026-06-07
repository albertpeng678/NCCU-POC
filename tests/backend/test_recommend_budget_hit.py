# tests/backend/test_recommend_budget_hit.py
"""POST /recommend 命中 career_budget → 直接讀整池回傳（0 次即時 AI）；未命中 → 落回即時路徑。"""
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

from backend.main import app


def _sync_with_asyncio_run(*a, **k):
    """模擬真實 build_recommendation_instrumented：同步函式內部用 asyncio.run 跑 async pipeline。
    若被 async handler 直接(非 to_thread)呼叫，會在運行中的 loop 裡 asyncio.run → RuntimeError。"""
    async def _coro():
        return {"career": "x", "courses": [{"course_id": "y", "name": "課", "group": "core", "rank": 1}],
                "batch_size": 10, "latency_ms": 1, "seed": 1}
    return asyncio.run(_coro()), 1


_BUDGET = {
    "courses": [{"course_id": "070415001", "name": "資料科學基礎", "group": "core", "rank": 1,
                 "department": "社科院", "teacher": "x", "credits": 3.0, "syllabus_url": "#",
                 "reason": {"lead": "適合", "points": []}}],
    "pool_size": 1, "built_at": "2026-06-08",
}


def test_post_recommend_hit_serves_budget_without_live_ai():
    with patch("backend.main.get_budget", AsyncMock(return_value=_BUDGET)) as gb, \
         patch("backend.main.get_pool", return_value=MagicMock()), \
         patch("backend.main.build_recommendation_instrumented", MagicMock()) as live, \
         patch("backend.main._background_log_and_judge", MagicMock()):
        resp = TestClient(app).post("/recommend", json={"career": "資料科學家"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert [c["course_id"] for c in data["courses"]] == ["070415001"]
    assert data["batch_size"] == 10
    assert "seed" in data
    gb.assert_awaited()
    live.assert_not_called()            # 命中 → 絕不跑即時 fan-out + stage2


def test_post_recommend_miss_falls_back_to_live():
    live_result = {"career": "資料科學家", "courses": [{"course_id": "y", "name": "課2", "group": "core", "rank": 1}],
                   "batch_size": 10, "latency_ms": 5, "seed": 1}
    with patch("backend.main.get_budget", AsyncMock(return_value=None)), \
         patch("backend.main.get_pool", return_value=MagicMock()), \
         patch("backend.main.build_recommendation_instrumented", MagicMock(return_value=(live_result, 3))) as live, \
         patch("backend.main._background_log_and_judge", MagicMock()):
        resp = TestClient(app).post("/recommend", json={"career": "資料科學家"})
    assert resp.status_code == 200, resp.text
    assert [c["course_id"] for c in resp.json()["courses"]] == ["y"]
    live.assert_called_once()           # 未命中 → 走即時


def test_post_recommend_live_path_no_running_loop_crash():
    # 清單外/未命中 → 走即時 build_recommendation_instrumented（內部 asyncio.run）。
    # 必須在執行緒跑（asyncio.to_thread），否則在 async handler 的 loop 內 asyncio.run → RuntimeError → 503。
    with patch("backend.main.get_budget", AsyncMock(return_value=None)), \
         patch("backend.main.get_pool", return_value=MagicMock()), \
         patch("backend.main.build_recommendation_instrumented", _sync_with_asyncio_run), \
         patch("backend.main._background_log_and_judge", MagicMock()):
        resp = TestClient(app).post("/recommend", json={"career": "資料科學家"})
    assert resp.status_code == 200, resp.text   # 不可因 asyncio.run-in-running-loop 崩成 503


def test_post_recommend_no_db_falls_back_to_live():
    live_result = {"career": "資料科學家", "courses": [], "batch_size": 10, "latency_ms": 1, "seed": 1}
    with patch("backend.main.get_pool", return_value=None), \
         patch("backend.main.build_recommendation_instrumented", MagicMock(return_value=(live_result, 0))) as live, \
         patch("backend.main._background_log_and_judge", MagicMock()):
        resp = TestClient(app).post("/recommend", json={"career": "資料科學家"})
    assert resp.status_code == 200, resp.text
    live.assert_called_once()           # 無 DB → get_budget 自然回 None → 即時
