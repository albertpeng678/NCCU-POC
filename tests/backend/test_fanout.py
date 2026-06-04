# tests/backend/test_fanout.py
"""fan-out 並行檢索：每技能一支 file_search、合併去重、容錯、池上限。
全程 mock async client，不打真 Gemini。"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from backend.recommend import (
    FANOUT_TOP_K,
    FANOUT_PER_SKILL,
    POOL_TARGET,
    fanout_query_skill_async,
)


def _fake_client(text):
    """client.aio.models.generate_content 回傳 .text=text 的假 client。"""
    resp = SimpleNamespace(text=text)
    aio = SimpleNamespace(models=SimpleNamespace(
        generate_content=AsyncMock(return_value=resp)))
    return SimpleNamespace(aio=aio)


def test_fanout_constants_have_expected_values():
    assert FANOUT_TOP_K == 8
    assert FANOUT_PER_SKILL == 5
    assert POOL_TARGET == 30


@pytest.mark.asyncio
async def test_fanout_query_skill_parses_array():
    client = _fake_client(
        '[{"course_id":"000211012","course_name":"政治學","relevance":"x"}]')
    out = await fanout_query_skill_async(client, "store", "PM", "分析")
    assert out[0]["course_id"] == "000211012"
    client.aio.models.generate_content.assert_awaited_once()


@pytest.mark.asyncio
async def test_fanout_query_skill_empty_text_returns_empty_list():
    client = _fake_client("沒有相關課程")
    out = await fanout_query_skill_async(client, "store", "PM", "分析")
    assert out == []
