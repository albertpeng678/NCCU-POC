# tests/backend/test_recommend.py
import json
from pathlib import Path
import pytest
from backend.models import RecommendRequest, CourseCard, RecommendResponse
from backend.recommend import deduplicate_by_prefix, join_metadata, load_careers, load_courses_meta, extract_json_array

def test_recommend_request_valid():
    r = RecommendRequest(career="產品經理(PM)")
    assert r.career == "產品經理(PM)"

def test_recommend_request_empty_career_fails():
    with pytest.raises(Exception):
        RecommendRequest(career="")

def test_course_card_has_required_fields():
    card = CourseCard(
        course_id="000211012",
        name="政治學",
        department="政治系",
        teacher="蔡中民",
        credits=3.0,
        reason={"lead": "培養分析能力", "points": [{"term": "分析", "detail": "系統性拆解問題"}]},
        syllabus_url="https://x.com/a",
    )
    assert card.course_id == "000211012"
    assert card.reason.lead == "培養分析能力"
    assert card.reason.points[0].term == "分析"
    assert card.syllabus_url == "https://x.com/a"


def test_career_skills_json_has_50_careers():
    data = json.loads(Path("backend/career_skills.json").read_text(encoding="utf-8"))
    assert len(data) >= 50


def test_career_skills_json_pm_has_skills():
    data = json.loads(Path("backend/career_skills.json").read_text(encoding="utf-8"))
    pm = data["產品經理(PM)"]
    assert "skills" in pm
    assert len(pm["skills"]) >= 5


def test_deduplicate_by_prefix_keeps_first():
    courses = [
        {"course_id": "000211012", "reason": "a"},
        {"course_id": "000211022", "reason": "b"},  # same 6-digit prefix — dropped
        {"course_id": "000216001", "reason": "c"},  # different prefix
    ]
    result = deduplicate_by_prefix(courses)
    assert len(result) == 2
    assert result[0]["course_id"] == "000211012"
    assert result[1]["course_id"] == "000216001"

def test_deduplicate_by_prefix_empty_list():
    assert deduplicate_by_prefix([]) == []

def test_join_metadata_enriches_cards():
    raw = [{"course_id": "000211012", "reason": "培養分析能力"}]
    meta = {
        "000211012": {
            "name": "政治學", "department": "政治系", "teacher": "蔡中民",
            "credits": 3.0, "syllabus_url": "https://x.com/a", "source": "syllabus"
        }
    }
    result = join_metadata(raw, meta)
    assert len(result) == 1
    assert result[0]["name"] == "政治學"
    assert result[0]["syllabus_url"] == "https://x.com/a"
    assert result[0]["reason"] == "培養分析能力"

def test_join_metadata_skips_missing_course_id():
    raw = [
        {"course_id": "000211012", "reason": "a"},
        {"course_id": "NOTEXIST99", "reason": "b"},
    ]
    meta = {
        "000211012": {
            "name": "政治學", "department": "政治系", "teacher": "蔡中民",
            "credits": 3.0, "syllabus_url": "https://x.com/a", "source": "syllabus"
        }
    }
    result = join_metadata(raw, meta)
    assert len(result) == 1

def test_load_careers_returns_dict():
    careers = load_careers()
    assert "產品經理(PM)" in careers
    assert "skills" in careers["產品經理(PM)"]

def test_load_courses_meta_returns_dict_or_empty():
    meta = load_courses_meta()
    assert isinstance(meta, dict)


def test_extract_json_array_plain():
    text = '[{"course_id": "000211012", "course_name": "政治學", "relevance": "x"}]'
    result = extract_json_array(text)
    assert len(result) == 1
    assert result[0]["course_id"] == "000211012"

def test_extract_json_array_markdown_fenced():
    text = '```json\n[{"course_id": "000211012", "relevance": "x"}]\n```'
    result = extract_json_array(text)
    assert len(result) == 1
    assert result[0]["course_id"] == "000211012"

def test_extract_json_array_with_surrounding_text():
    text = 'Here are the courses:\n```json\n[{"course_id": "000216001"}]\n```\nDone.'
    result = extract_json_array(text)
    assert len(result) == 1
    assert result[0]["course_id"] == "000216001"

def test_extract_json_array_empty_or_invalid_returns_empty():
    assert extract_json_array("no json here") == []
    assert extract_json_array("") == []
