# tests/backend/test_qa_structured_api.py
"""POST /qa 在 QA_MODE=replay 下走 answer_question_structured，回乾淨答案、不含 JSON 鷹架。"""
import os
import importlib
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_replay(monkeypatch):
    monkeypatch.setenv("QA_MODE", "replay")
    monkeypatch.setenv("GEMINI_QA_MODEL", "gemini-3.5-flash")
    monkeypatch.setenv("ALLOWED_ORIGIN", "*")
    import backend.main as m
    importlib.reload(m)

    def fake_structured(client, store, question, history=None, model="gemini-3.5-flash"):
        return {
            "answer": "**資料探勘** 是入門好課。",
            "followup_suggestions": ["這門課難嗎？"],
            "citations_course_ids": [],
            "latency_ms": 10,
        }
    monkeypatch.setattr(m, "answer_question_structured", fake_structured)
    return TestClient(m.app)


def test_post_qa_replay_returns_clean_answer(client_replay):
    resp = client_replay.post("/qa", json={"question": "推薦資料科學課程"})
    assert resp.status_code == 200
    data = resp.json()
    assert "資料探勘" in data["answer"]
    assert "```json" not in data["answer"]
    assert '{"answer"' not in data["answer"]
    assert data["followup_suggestions"] == ["這門課難嗎？"]
