# tests/backend/test_recommend_async.py
"""recommend.py 階段函式 async 化テスト。
- derive_skills_for_career_async: 改用 OpenAI _openai_structured（mock _openai_structured）。
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from backend.recommend import (
    derive_skills_for_career_async,
    _DerivedSkills,
)


@pytest.mark.asyncio
async def test_derive_skills_async_parses_json_array():
    """derive_skills_for_career_async 透過 _openai_structured 回 _DerivedSkills → 回 list[str]。"""
    fake_output = _DerivedSkills(is_legitimate_career=True, skills=["公共衛生", "基礎管理", "人際溝通"])
    with patch("backend.recommend._openai_structured", new=AsyncMock(return_value=fake_output)):
        skills = await derive_skills_for_career_async(MagicMock(), "流行病學家")
    assert skills == ["公共衛生", "基礎管理", "人際溝通"]


@pytest.mark.asyncio
async def test_derive_skills_async_returns_none_on_garbage():
    """_openai_structured 回 skills=[] → derive 回 None（no_match）。"""
    fake_output = _DerivedSkills(is_legitimate_career=False, skills=[])
    with patch("backend.recommend._openai_structured", new=AsyncMock(return_value=fake_output)):
        skills = await derive_skills_for_career_async(MagicMock(), "asdfqwer")
    assert skills is None
