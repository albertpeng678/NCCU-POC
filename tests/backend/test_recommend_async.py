# tests/backend/test_recommend_async.py
"""recommend.py 階段函式 async 化テスト。
- derive_skills_for_career_async: 改用 OpenAI _openai_structured（mock _openai_structured）。
- stage1_retrieve_async / stage2_group_async: 仍是 Gemini legacy helpers（mock generate_content）。
"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch, MagicMock
from pydantic import BaseModel
from backend.recommend import (
    derive_skills_for_career_async,
    stage1_retrieve_async,
    stage2_group_async,
    _DerivedSkills,
)


def _fake_gemini_client(text=None, parsed=None):
    """建一個 client.aio.models.generate_content 為 AsyncMock 的假 Gemini client。"""
    resp = SimpleNamespace(text=text, parsed=parsed)
    aio = SimpleNamespace(models=SimpleNamespace(
        generate_content=AsyncMock(return_value=resp)))
    return SimpleNamespace(aio=aio)


@pytest.mark.asyncio
async def test_derive_skills_async_parses_json_array():
    """derive_skills_for_career_async 透過 _openai_structured 回 _DerivedSkills → 回 list[str]。"""
    fake_output = _DerivedSkills(skills=["公共衛生", "基礎管理", "人際溝通"])
    with patch("backend.recommend._openai_structured", new=AsyncMock(return_value=fake_output)):
        skills = await derive_skills_for_career_async(MagicMock(), "流行病學家")
    assert skills == ["公共衛生", "基礎管理", "人際溝通"]


@pytest.mark.asyncio
async def test_derive_skills_async_returns_none_on_garbage():
    """_openai_structured 回 skills=[] → derive 回 None（no_match）。"""
    fake_output = _DerivedSkills(skills=[])
    with patch("backend.recommend._openai_structured", new=AsyncMock(return_value=fake_output)):
        skills = await derive_skills_for_career_async(MagicMock(), "asdfqwer")
    assert skills is None


@pytest.mark.asyncio
async def test_stage1_retrieve_async_extracts_array():
    """stage1_retrieve_async 仍是 Gemini legacy helper（未在主 pipeline 使用）。"""
    client = _fake_gemini_client(
        text='[{"course_id":"000211012","course_name":"政治學","relevance":"x"}]')
    out = await stage1_retrieve_async(client, "store", "PM", ["分析"])
    assert out[0]["course_id"] == "000211012"
    client.aio.models.generate_content.assert_awaited_once()


@pytest.mark.asyncio
async def test_stage2_group_async_returns_parsed():
    """stage2_group_async 仍是 Gemini legacy helper（未在主 pipeline 使用）。"""
    from backend.recommend import _Stage2Output, _Groups
    parsed = _Stage2Output(groups=_Groups(core=[], supporting=[], extended=[]))
    client = _fake_gemini_client(parsed=parsed)
    out = await stage2_group_async(client, "PM", ["分析"], [])
    assert out is parsed
    client.aio.models.generate_content.assert_awaited_once()
