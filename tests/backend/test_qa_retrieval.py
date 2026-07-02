import asyncio
import backend.qa_retrieval as qr


def test_happy(monkeypatch):
    async def fr(q): return {"queries": ["x"], "department": None, "college": None, "degree_level": None}
    async def fs(qs, slots, vs, **k): return [{"course_id": "A", "filename": "A.txt", "content": [{"text": "hi"}], "attributes": {"course_id": "A", "dept_canonical": "歷史學系"}}]
    async def fk(q, c, top_k=10): return c
    monkeypatch.setattr(qr, "rewrite_to_queries", fr)
    monkeypatch.setattr(qr, "multi_query_search", fs)
    monkeypatch.setattr(qr, "rerank_courses", fk)
    ctx, ids = asyncio.run(qr.retrieve_and_rerank("q", "vs"))
    assert ctx and ids == ["A"]


def test_empty_search(monkeypatch):
    async def fr(q): return {"queries": ["x"], "department": None, "college": None, "degree_level": None}
    async def fs(*a, **k): return []
    monkeypatch.setattr(qr, "rewrite_to_queries", fr)
    monkeypatch.setattr(qr, "multi_query_search", fs)
    assert asyncio.run(qr.retrieve_and_rerank("q", "vs")) == (None, [])
