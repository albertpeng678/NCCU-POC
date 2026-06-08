# tests/backend/test_recommend_loop_fix.py
"""POST /recommend pipeline 必須在『呼叫端 event loop』跑（不可用 asyncio.run 開臨時 loop）。

根因（Session 7 🔴）：build_recommendation_instrumented 內用 asyncio.run() 在 worker thread 開臨時 loop，
跑完即關 → 污染共用 _client.aio → 之後 /recommend/stream、/qa/stream 噴「Event loop is closed」。
修法：提供 async 版 build_recommendation_instrumented_async，POST /recommend 直接 await（主 loop）。
本測試鎖定根因：async pipeline 的兩個步驟都必須在『呼叫端 loop』執行。
"""
import asyncio
import pytest

import backend.recommend as recommend


@pytest.mark.asyncio
async def test_instrumented_async_runs_on_caller_loop(monkeypatch):
    loops = []

    async def fake_fanout(client, store, career, skills):
        loops.append(id(asyncio.get_running_loop()))
        return [{"course_id": "000211012"}]

    async def fake_annotate(client, career, skills, candidates):
        loops.append(id(asyncio.get_running_loop()))
        return "RANKED"

    monkeypatch.setattr(recommend, "fanout_retrieve_async", fake_fanout)
    monkeypatch.setattr(recommend, "stage2_annotate_pool_async", fake_annotate)
    monkeypatch.setattr(recommend, "build_ranked_courses",
                        lambda ranked, candidates, meta: [{"course_id": "000211012", "group": "core"}])
    monkeypatch.setattr(recommend, "load_courses_meta", lambda: {})
    monkeypatch.setattr(recommend, "load_careers", lambda: {"PM": {"skills": ["s"]}})

    caller_loop = id(asyncio.get_running_loop())
    result, pool_count = await recommend.build_recommendation_instrumented_async(
        object(), "store", "PM", seed=1, skills=["s"]
    )

    assert result["courses"] == [{"course_id": "000211012", "group": "core"}]
    assert result["seed"] == 1
    assert pool_count == 1
    # 核心斷言：兩個 async 步驟都在呼叫端 loop（無臨時 asyncio.run loop）
    assert loops == [caller_loop, caller_loop]


@pytest.mark.asyncio
async def test_instrumented_async_raises_on_empty_candidates(monkeypatch):
    """空候選 → ValueError（與舊同步版行為一致，呼叫端據此回 503/no_match）。"""
    async def fake_fanout(client, store, career, skills):
        return []

    monkeypatch.setattr(recommend, "fanout_retrieve_async", fake_fanout)
    monkeypatch.setattr(recommend, "load_courses_meta", lambda: {})
    monkeypatch.setattr(recommend, "load_careers", lambda: {"PM": {"skills": ["s"]}})

    with pytest.raises(ValueError):
        await recommend.build_recommendation_instrumented_async(
            object(), "store", "PM", seed=1, skills=["s"]
        )
