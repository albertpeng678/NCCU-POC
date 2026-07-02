import asyncio

import backend.qa_rewrite as qr


def test_clamp_queries_to_5():
    assert qr._clamp_queries(["a", "b", "c", "d", "e", "f"]) == ["a", "b", "c", "d", "e"]
    assert qr._clamp_queries([]) == []


def test_rewrite_fallback_on_none(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("llm down")

    monkeypatch.setattr(qr, "_call_rewrite_llm", boom)
    out = asyncio.run(qr.rewrite_to_queries("推薦我傳碩十堂課"))
    assert out["queries"] == ["推薦我傳碩十堂課"]
    assert out["department"] is None and out["college"] is None


def test_rewrite_returns_none_path(monkeypatch):
    async def none_path(*a, **k):
        return None

    monkeypatch.setattr(qr, "_call_rewrite_llm", none_path)
    out = asyncio.run(qr.rewrite_to_queries("推薦我傳碩十堂課"))
    assert out == {
        "queries": ["推薦我傳碩十堂課"],
        "department": None,
        "college": None,
        "degree_level": None,
    }


def test_clamp_strips_blanks():
    assert qr._clamp_queries(["  ", "a", "", "  b  "]) == ["a", "b"]
