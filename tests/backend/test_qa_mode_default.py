# tests/backend/test_qa_mode_default.py
"""QA_MODE 未設時預設走 2.5 串流（stream），3.5 replay 為選項。"""
import importlib


def test_default_qa_mode_is_stream(monkeypatch):
    monkeypatch.delenv("QA_MODE", raising=False)
    monkeypatch.setenv("ALLOWED_ORIGIN", "*")
    import backend.main as m
    importlib.reload(m)
    assert m._QA_MODE == "stream"
