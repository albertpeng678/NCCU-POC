# tests/backend/test_recommend_stream.py
"""stream_recommendation async generator：驗證 5 階段事件序列、result/no_match 分支。
mock async stage 函式（不打真 Gemini）。"""
import pytest
from unittest.mock import patch, AsyncMock
from backend.recommend import stream_recommendation, _Stage2Output, _Groups, _CourseItem, _ReasonPoint


def _stage2_with_core(course_id):
    item = _CourseItem(
        course_id=course_id, reason_lead="總述",
        reason_points=[_ReasonPoint(term="分析", detail="拆解問題")],
    )
    return _Stage2Output(groups=_Groups(core=[item], supporting=[], extended=[]))


async def _collect(gen):
    return [ev async for ev in gen]


@pytest.mark.asyncio
async def test_stream_known_career_emits_5_stages_and_result():
    meta = {"000211012": {"name": "政治學", "department": "政治系", "teacher": "蔡",
                          "credits": 3.0, "syllabus_url": "https://x/a"}}
    with patch("backend.recommend.load_courses_meta", return_value=meta), \
         patch("backend.recommend.stage1_retrieve_async",
               new=AsyncMock(return_value=[{"course_id": "000211012",
                                            "course_name": "政治學", "relevance": "x"}])), \
         patch("backend.recommend.stage2_group_async",
               new=AsyncMock(return_value=_stage2_with_core("000211012"))):
        events = await _collect(
            stream_recommendation(client=object(), store_name="s",
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
    assert r["groups"]["core"][0]["name"] == "政治學"
    assert r["seed"] == 1


@pytest.mark.asyncio
async def test_stream_open_career_no_skills_emits_no_match():
    with patch("backend.recommend.derive_skills_for_career_async",
               new=AsyncMock(return_value=None)):
        events = await _collect(
            stream_recommendation(client=object(), store_name="s",
                                  career="asdfqwer", seed=1, skills=None))
    assert any(e["event"] == "no_match" for e in events)
    assert not any(e["event"] == "result" for e in events)
    # understand 階段有 start/done，retrieve 不應出現（提前結束）
    keys = [e["data"]["key"] for e in events if e["event"] == "stage"]
    assert "retrieve" not in keys


@pytest.mark.asyncio
async def test_stream_open_career_empty_retrieval_emits_no_match():
    with patch("backend.recommend.derive_skills_for_career_async",
               new=AsyncMock(return_value=["公共衛生"])), \
         patch("backend.recommend.stage1_retrieve_async",
               new=AsyncMock(return_value=[])):
        events = await _collect(
            stream_recommendation(client=object(), store_name="s",
                                  career="清潔工", seed=1, skills=None))
    assert any(e["event"] == "no_match" for e in events)
    assert not any(e["event"] == "result" for e in events)
