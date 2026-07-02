import asyncio

import pytest
from pydantic import ValidationError

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


def test_queries_out_college_degree_level_are_literal_enums():
    # canonical Literal 值可過
    ok = qr.QueriesOut(queries=["q"], college="傳播學院", degree_level="碩士")
    assert ok.college == "傳播學院"
    assert ok.degree_level == "碩士"

    # 非法值（不在 12 學院 / 5 學制值域內）要 raise ValidationError，
    # 證明 schema 層面已收斂值域（不再是自由文字，降低複合縮寫拆法漂移）。
    with pytest.raises(ValidationError):
        qr.QueriesOut(queries=["q"], college="不存在學院")
    with pytest.raises(ValidationError):
        qr.QueriesOut(queries=["q"], degree_level="不存在學制")
