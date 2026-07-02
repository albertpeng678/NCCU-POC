import asyncio

import backend.qa_multiquery as mq


def test_passes_array_clamped_and_filter(monkeypatch):
    calls = []

    async def fake_search(**k):
        calls.append(k)
        return type("R", (), {"data": [{"id": 1}]})()

    monkeypatch.setattr(mq, "_vs_search", fake_search)
    out = asyncio.run(
        mq.multi_query_search(["a", "b", "c", "d", "e", "f"], {"department": "歷史學系"}, "vs_x")
    )
    assert len(calls[0]["query"]) == 5            # 陣列 clamp 到 5
    assert calls[0]["filters"] is not None       # 系所→帶輔助 filter
    assert out == [{"id": 1}]


def test_empty_with_filter_retries_without(monkeypatch):
    seq = [[], [{"id": 9}]]
    calls = []

    async def fake_search(**k):
        calls.append(k)
        return type("R", (), {"data": seq.pop(0)})()

    monkeypatch.setattr(mq, "_vs_search", fake_search)
    out = asyncio.run(mq.multi_query_search(["a"], {"college": "文學院"}, "vs"))
    assert calls[0]["filters"] is not None and calls[1]["filters"] is None  # 撈空去 filter 重搜
    assert out == [{"id": 9}]
