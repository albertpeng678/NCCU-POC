# tests/backend/test_qa_mode_default.py
"""QA_MODE 未設時預設走 3.5 結構化串流（stream35），stream(2.5 fallback) 與 replay 為可選項。"""
import importlib


def test_default_qa_mode_is_stream35(monkeypatch):
    monkeypatch.delenv("QA_MODE", raising=False)
    monkeypatch.setenv("ALLOWED_ORIGIN", "*")
    import backend.main as m
    importlib.reload(m)
    assert m._QA_MODE == "stream35"
