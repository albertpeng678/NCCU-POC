# tests/backend/test_qa_mode_default.py
"""QA_MODE 未設時預設走 stream（OpenAI Responses API 串流）。stream35(3.5 Gemini) 與 replay 為可選項。

Phase 2 注：stream 模式已從 Gemini generate_content_stream 切至 OpenAI Responses API。
"""
import importlib


def test_default_qa_mode_is_stream(monkeypatch):
    monkeypatch.delenv("QA_MODE", raising=False)
    monkeypatch.setenv("ALLOWED_ORIGIN", "*")
    import backend.main as m
    importlib.reload(m)
    assert m._QA_MODE == "stream"
