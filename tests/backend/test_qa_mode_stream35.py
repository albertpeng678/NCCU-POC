# tests/backend/test_qa_mode_stream35.py
"""QA_MODE 預設為 stream35（3.5 結構化串流）；/qa/stream 走 stream_answer_structured。"""
import importlib
import pytest
from fastapi.testclient import TestClient


def test_default_qa_mode_is_stream35(monkeypatch):
    monkeypatch.delenv("QA_MODE", raising=False)
    monkeypatch.setenv("ALLOWED_ORIGIN", "*")
    import backend.main as m
    importlib.reload(m)
    assert m._QA_MODE == "stream35"


def test_stream35_uses_structured_generator(monkeypatch):
    monkeypatch.setenv("QA_MODE", "stream35")
    monkeypatch.setenv("ALLOWED_ORIGIN", "*")
    import backend.main as m
    importlib.reload(m)

    used = {"structured": False, "plain": False}

    async def fake_structured(client, store, question, history=None, model="gemini-3.5-flash"):
        used["structured"] = True
        yield {"event": "token", "data": {"text": "嗨"}}
        yield {"event": "done", "data": {"course_ids": [], "answer_text":
               '{"answer":"嗨","followup_suggestions":[]}'}}

    async def fake_plain(client, store, question, history=None):
        used["plain"] = True
        yield {"event": "done", "data": {"course_ids": [], "answer_text": "{}"}}

    monkeypatch.setattr(m, "stream_answer_structured", fake_structured)
    monkeypatch.setattr(m, "stream_answer", fake_plain)

    with TestClient(m.app).stream("GET", "/qa/stream?question=測試") as resp:
        body = "".join(c for c in resp.iter_text())
    assert used["structured"] is True
    assert used["plain"] is False
    assert "event: done" in body
