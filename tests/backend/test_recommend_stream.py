# tests/backend/test_recommend_stream.py
"""stream_recommendation async generator：驗證 5 階段事件序列、result/no_match 分支、
result 帶扁平 ranked courses。mock async fan-out / annotate（不打真 Gemini）。"""
import pytest
from unittest.mock import patch, AsyncMock
from backend.recommend import (
    stream_recommendation, _RankedOutput, _RankedItem,
)


def _ranked_core(course_id):
    return _RankedOutput(courses=[
        _RankedItem(course_id=course_id, group="core", reason_lead="總述",
                    reason_points=[{"term": "分析", "detail": "拆解問題"},
                                   {"term": "建模", "detail": "量化"}])
    ])


async def _collect(gen):
    return [ev async for ev in gen]


@pytest.mark.asyncio
async def test_stream_known_career_emits_5_stages_and_flat_result():
    meta = {"000211012": {"name": "政治學", "department": "政治系", "teacher": "蔡",
                          "credits": 3.0, "syllabus_url": "https://x/a"}}
    pool = [{"course_id": "000211012", "course_name": "政治學", "relevance": "x"}]
    with patch("backend.recommend.load_courses_meta", return_value=meta), \
         patch("backend.recommend.fanout_retrieve_async",
               new=AsyncMock(return_value=pool)), \
         patch("backend.recommend.stage2_annotate_pool_async",
               new=AsyncMock(return_value=_ranked_core("000211012"))):
        events = await _collect(
            stream_recommendation(client=object(), vs_id="vs_test",
                                  career="產品經理(PM)", seed=1, skills=None))

    keys = [e["data"]["key"] for e in events if e["event"] == "stage"]
    assert keys == ["understand", "understand", "retrieve", "retrieve",
                    "filter", "filter", "compose", "compose",
                    "finalize", "finalize"]
    statuses = [e["data"]["status"] for e in events if e["event"] == "stage"]
    assert statuses == ["start", "done"] * 5

    result_events = [e for e in events if e["event"] == "result"]
    assert len(result_events) == 1
    r = result_events[0]["data"]
    assert r["career"] == "產品經理(PM)"
    assert r["courses"][0]["name"] == "政治學"
    assert r["courses"][0]["group"] == "core"
    assert r["batch_size"] == 10
    assert r["seed"] == 1


@pytest.mark.asyncio
async def test_stream_open_career_no_skills_emits_no_match():
    with patch("backend.recommend.derive_skills_for_career_async",
               new=AsyncMock(return_value=None)):
        events = await _collect(
            stream_recommendation(client=object(), vs_id="vs_test",
                                  career="asdfqwer", seed=1, skills=None))
    assert any(e["event"] == "no_match" for e in events)
    assert not any(e["event"] == "result" for e in events)
    keys = [e["data"]["key"] for e in events if e["event"] == "stage"]
    assert "retrieve" not in keys


@pytest.mark.asyncio
async def test_stream_open_career_empty_pool_emits_no_match():
    with patch("backend.recommend.derive_skills_for_career_async",
               new=AsyncMock(return_value=["公共衛生"])), \
         patch("backend.recommend.fanout_retrieve_async",
               new=AsyncMock(return_value=[])):
        events = await _collect(
            stream_recommendation(client=object(), vs_id="vs_test",
                                  career="清潔工", seed=1, skills=None))
    assert any(e["event"] == "no_match" for e in events)
    assert not any(e["event"] == "result" for e in events)


@pytest.mark.asyncio
async def test_stream_known_career_empty_pool_emits_error():
    # 清單內但池空 → error 事件（與 POST 行為一致）
    with patch("backend.recommend.load_courses_meta", return_value={}), \
         patch("backend.recommend.fanout_retrieve_async",
               new=AsyncMock(return_value=[])):
        events = await _collect(
            stream_recommendation(client=object(), vs_id="vs_test",
                                  career="產品經理(PM)", seed=1, skills=None))
    assert any(e["event"] == "error" for e in events)
    assert not any(e["event"] == "result" for e in events)
