import asyncio

import backend.qa_rerank as rr


def test_reorders_by_llm(monkeypatch):
    cands = [
        {"course_id": "A", "name": "n1", "text": "t1"},
        {"course_id": "B", "name": "n2", "text": "t2"},
    ]

    async def fake(q, c):
        return type("O", (), {"ranked_course_ids": ["B", "A"]})()

    monkeypatch.setattr(rr, "_call_rerank_llm", fake)
    out = asyncio.run(rr.rerank_courses("q", cands, top_k=2))
    assert [x["course_id"] for x in out] == ["B", "A"]


def test_failopen(monkeypatch):
    cands = [{"course_id": "A"}, {"course_id": "B"}]

    async def boom(q, c):
        raise RuntimeError()

    monkeypatch.setattr(rr, "_call_rerank_llm", boom)
    assert asyncio.run(rr.rerank_courses("q", cands, top_k=5)) == cands


def test_empty():
    assert asyncio.run(rr.rerank_courses("q", [], top_k=5)) == []


def test_excludes_irrelevant(monkeypatch):
    cands = [
        {"course_id": "A", "name": "n1", "text": "t1"},
        {"course_id": "B", "name": "n2", "text": "t2"},
    ]

    async def fake(q, c):
        return type("O", (), {"ranked_course_ids": ["B"]})()

    monkeypatch.setattr(rr, "_call_rerank_llm", fake)
    out = asyncio.run(rr.rerank_courses("q", cands, top_k=5))
    assert [x["course_id"] for x in out] == ["B"]


def test_empty_ranking_falls_back_to_candidates(monkeypatch):
    cands = [{"course_id": "A"}, {"course_id": "B"}]

    async def fake(q, c):
        return type("O", (), {"ranked_course_ids": []})()

    monkeypatch.setattr(rr, "_call_rerank_llm", fake)
    out = asyncio.run(rr.rerank_courses("q", cands, top_k=5))
    assert out == cands[:5]
