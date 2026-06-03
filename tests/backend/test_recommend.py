# tests/backend/test_recommend.py
import pytest
from backend.models import RecommendRequest, CourseCard, RecommendResponse

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
        reason="培養分析能力",
        syllabus_url="https://x.com/a",
    )
    assert card.course_id == "000211012"
    assert card.syllabus_url == "https://x.com/a"
