# tests/backend/test_qa_stream_replay.py
"""/qa/stream 在 QA_MODE=replay：送 stage 事件 + 單一 done(乾淨 answer)，不逐 token、不含鷹架。"""
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
            "answer": "**統計學** 是資料科學的地基。",
            "followup_suggestions": ["先修需求？"],
            "citations_course_ids": [],
            "latency_ms": 5,
        }
    monkeypatch.setattr(m, "answer_question_structured", fake_structured)
    return TestClient(m.app)


def test_stream_replay_emits_stage_and_done(client_replay):
    with client_replay.stream("GET", "/qa/stream?question=推薦課程") as resp:
        body = "".join(chunk for chunk in resp.iter_text())
    assert "event: stage" in body          # 立即 stage 事件（防前端 guard 誤觸 fallback）
    assert "event: done" in body
    assert "統計學" in body
    assert "```json" not in body
    assert "event: token" not in body       # replay 不逐 token
