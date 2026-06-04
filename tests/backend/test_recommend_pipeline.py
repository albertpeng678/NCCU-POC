# tests/backend/test_recommend_pipeline.py
"""build_recommendation(_instrumented) 走 fan-out + 整池標註 → 扁平 courses。
mock async retrieve/annotate，不打真 Gemini。"""
import pytest
from unittest.mock import patch, AsyncMock
from backend.recommend import (
    build_recommendation,
    build_recommendation_instrumented,
    _RankedOutput, _RankedItem,
)


def _meta(*ids_names):
    return {cid: {"name": name, "department": "系", "teacher": "師",
                  "credits": 3.0, "syllabus_url": f"https://x/{cid}"}
            for cid, name in ids_names}


def _ranked(*triples):
    return _RankedOutput(courses=[
        _RankedItem(course_id=cid, group=grp, reason_lead="總述",
                    reason_points=[{"term": "分析", "detail": "拆解"},
                                   {"term": "建模", "detail": "量化"}])
        for cid, grp in triples
    ])


def test_build_recommendation_returns_flat_courses():
    meta = _meta(("000010011", "A"), ("000020011", "B"))
    pool = [{"course_id": "000010011", "course_name": "A", "relevance": "x"},
            {"course_id": "000020011", "course_name": "B", "relevance": "x"}]
    with patch("backend.recommend.load_courses_meta", return_value=meta), \
         patch("backend.recommend.load_careers",
               return_value={"PM": {"skills": ["分析", "溝通"]}}), \
         patch("backend.recommend.fanout_retrieve_async",
               new=AsyncMock(return_value=pool)), \
         patch("backend.recommend.stage2_annotate_pool_async",
               new=AsyncMock(return_value=_ranked(("000010011", "core"),
                                                  ("000020011", "supporting")))):
        result = build_recommendation(object(), "store", "PM", seed=1)
    assert result["career"] == "PM"
    assert "courses" in result
    assert result["batch_size"] == 10
    assert result["seed"] == 1
    assert [c["name"] for c in result["courses"]] == ["A", "B"]
    assert result["courses"][0]["group"] == "core"
    assert result["courses"][0]["rank"] == 0


def test_build_recommendation_empty_pool_raises():
    with patch("backend.recommend.load_courses_meta", return_value={}), \
         patch("backend.recommend.load_careers",
               return_value={"PM": {"skills": ["分析"]}}), \
         patch("backend.recommend.fanout_retrieve_async",
               new=AsyncMock(return_value=[])):
        with pytest.raises(ValueError):
            build_recommendation(object(), "store", "PM", seed=1)


def test_instrumented_returns_pool_count():
    meta = _meta(("000010011", "A"))
    pool = [{"course_id": "000010011", "course_name": "A", "relevance": "x"}]
    with patch("backend.recommend.load_courses_meta", return_value=meta), \
         patch("backend.recommend.load_careers",
               return_value={"PM": {"skills": ["分析"]}}), \
         patch("backend.recommend.fanout_retrieve_async",
               new=AsyncMock(return_value=pool)), \
         patch("backend.recommend.stage2_annotate_pool_async",
               new=AsyncMock(return_value=_ranked(("000010011", "core")))):
        result, count = build_recommendation_instrumented(object(), "store", "PM", seed=1)
    assert count == 1
    assert result["courses"][0]["name"] == "A"


def test_build_recommendation_unknown_career_with_skills_ok():
    # 清單外但傳入 skills → 不查 careers、直接跑
    meta = _meta(("000010011", "A"))
    pool = [{"course_id": "000010011", "course_name": "A", "relevance": "x"}]
    with patch("backend.recommend.load_courses_meta", return_value=meta), \
         patch("backend.recommend.load_careers", return_value={}), \
         patch("backend.recommend.fanout_retrieve_async",
               new=AsyncMock(return_value=pool)), \
         patch("backend.recommend.stage2_annotate_pool_async",
               new=AsyncMock(return_value=_ranked(("000010011", "core")))):
        result = build_recommendation(object(), "store", "記者", seed=1, skills=["寫作"])
    assert result["courses"][0]["name"] == "A"
