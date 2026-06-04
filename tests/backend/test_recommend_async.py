# tests/backend/test_recommend_async.py
"""recommend.py 階段函式 async 化：須呼叫 client.aio.models.generate_content（不阻塞）。
mock 一個 fake async client，斷言被 await 到且回傳被正確解析。"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from backend.recommend import (
    derive_skills_for_career_async,
    stage1_retrieve_async,
    stage2_group_async,
)


def _fake_client(text=None, parsed=None):
    """建一個 client.aio.models.generate_content 為 AsyncMock 的假 client。"""
    resp = SimpleNamespace(text=text, parsed=parsed)
    aio = SimpleNamespace(models=SimpleNamespace(
        generate_content=AsyncMock(return_value=resp)))
    return SimpleNamespace(aio=aio)


@pytest.mark.asyncio
async def test_derive_skills_async_parses_json_array():
    client = _fake_client(text='["公共衛生","基礎管理","人際溝通"]')
    skills = await derive_skills_for_career_async(client, "流行病學家")
    assert skills == ["公共衛生", "基礎管理", "人際溝通"]
    client.aio.models.generate_content.assert_awaited_once()


@pytest.mark.asyncio
async def test_derive_skills_async_returns_none_on_garbage():
    client = _fake_client(text="[]")
    skills = await derive_skills_for_career_async(client, "asdfqwer")
    assert skills is None


@pytest.mark.asyncio
async def test_stage1_retrieve_async_extracts_array():
    client = _fake_client(
        text='[{"course_id":"000211012","course_name":"政治學","relevance":"x"}]')
    out = await stage1_retrieve_async(client, "store", "PM", ["分析"])
    assert out[0]["course_id"] == "000211012"
    client.aio.models.generate_content.assert_awaited_once()


@pytest.mark.asyncio
async def test_stage2_group_async_returns_parsed():
    from backend.recommend import _Stage2Output, _Groups
    parsed = _Stage2Output(groups=_Groups(core=[], supporting=[], extended=[]))
    client = _fake_client(parsed=parsed)
    out = await stage2_group_async(client, "PM", ["分析"], [])
    assert out is parsed
    client.aio.models.generate_content.assert_awaited_once()
