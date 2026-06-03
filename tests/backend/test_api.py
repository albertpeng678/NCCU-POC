# tests/backend/test_api.py
import pytest
import json
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi.testclient import TestClient

@pytest.fixture
def client():
    with patch("backend.main.build_recommendation_instrumented") as mock_build, \
         patch("backend.main.evaluate_recommendation", new=AsyncMock(return_value=None)):
        mock_build.return_value = (
            {
                "career": "產品經理(PM)",
                "groups": {
                    "core": [{"course_id": "000211012", "name": "政治學", "department": "政治系",
                              "teacher": "蔡中民", "credits": 3.0,
                              "reason": {"lead": "培養分析能力", "points": [{"term": "分析", "detail": "拆解問題"}]},
                              "syllabus_url": "https://x.com/a"}],
                    "supporting": [],
                    "extended": [],
                },
                "latency_ms": 1200,
            },
            15,  # stage1_count
        )
        from backend.main import app
        yield TestClient(app), mock_build


def test_recommend_valid_career(client):
    tc, mock_fn = client
    resp = tc.post("/recommend", json={"career": "產品經理(PM)"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["career"] == "產品經理(PM)"
    assert "groups" in data
    assert "core" in data["groups"]
    mock_fn.assert_called_once()


def test_recommend_invalid_career_returns_no_match(client):
    # 清單外職涯不再回 400，改由 LLM 推導技能；無法推導時回 200 + no_match=True
    tc, _ = client
    with patch("backend.main.derive_skills_for_career", return_value=None):
        resp = tc.post("/recommend", json={"career": "不存在的職業XYZ"})
    assert resp.status_code == 200
    assert resp.json().get("no_match") is True


def test_recommend_empty_career_returns_422(client):
    tc, _ = client
    resp = tc.post("/recommend", json={"career": ""})
    assert resp.status_code == 422


def test_health_check(client):
    tc, _ = client
    resp = tc.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
