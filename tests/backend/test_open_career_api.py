# tests/backend/test_open_career_api.py
# 測試清單外職涯分支：notice / no_match / 已知職涯不帶 notice
from fastapi.testclient import TestClient
import backend.main as M


def test_unknown_career_no_match(monkeypatch):
    monkeypatch.setattr(M, "derive_skills_for_career", lambda c, career: None)
    client = TestClient(M.app)
    r = client.post("/recommend", json={"career": "asdfqwer"})
    assert r.status_code == 200
    assert r.json().get("no_match") is True


def test_unknown_career_with_skills_recommends(monkeypatch):
    monkeypatch.setattr(M, "derive_skills_for_career", lambda c, career: ["公共衛生"])
    monkeypatch.setattr(
        M,
        "build_recommendation_instrumented",
        lambda client, store, career, seed, skills=None: (
            {
                "career": career,
                "groups": {
                    "core": [
                        {
                            "course_id": "000211012",
                            "name": "公共衛生概論",
                            "department": "X系",
                            "teacher": "T",
                            "credits": 3.0,
                            "reason": {
                                "lead": "l",
                                "points": [{"term": "a", "detail": "b"}],
                            },
                            "syllabus_url": "http://x",
                        }
                    ],
                    "supporting": [],
                    "extended": [],
                },
                "latency_ms": 1,
                "seed": seed,
            },
            1,
        ),
    )
    client = TestClient(M.app)
    r = client.post("/recommend", json={"career": "清潔工"})
    assert r.status_code == 200
    body = r.json()
    assert "可轉移能力" in (body.get("notice") or "")


def test_known_career_no_notice(monkeypatch):
    # 50 種內職涯不應觸發 derive，且不帶 notice
    monkeypatch.setattr(
        M,
        "build_recommendation_instrumented",
        lambda client, store, career, seed, skills=None: (
            {
                "career": career,
                "groups": {"core": [], "supporting": [], "extended": []},
                "latency_ms": 1,
                "seed": seed,
            },
            0,
        ),
    )
    client = TestClient(M.app)
    # 用一個真實存在於 career_skills.json 的職涯
    r = client.post("/recommend", json={"career": "產品經理(PM)"})
    assert r.status_code == 200
    assert r.json().get("notice") in (None, "")
    assert r.json().get("no_match") in (None, False)
