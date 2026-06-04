# tests/backend/test_ranked.py
"""扁平 ranked 標註：schema、annotate-pool async、build_ranked_courses 轉換。
全程 mock，不打真 Gemini。"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from backend.recommend import (
    _RankedItem,
    _RankedOutput,
    stage2_annotate_pool_async,
)


def test_ranked_item_schema_fields():
    item = _RankedItem(
        course_id="000211012",
        group="core",
        reason_lead="總述",
        reason_points=[{"term": "分析", "detail": "拆解問題"},
                       {"term": "建模", "detail": "量化決策"}],
    )
    assert item.course_id == "000211012"
    assert item.group == "core"
    assert item.reason_points[0].term == "分析"


def test_ranked_output_is_list_of_items():
    out = _RankedOutput(courses=[
        _RankedItem(course_id="000211012", group="core",
                    reason_lead="x", reason_points=[]),
    ])
    assert len(out.courses) == 1


def _fake_client(parsed):
    resp = SimpleNamespace(parsed=parsed)
    aio = SimpleNamespace(models=SimpleNamespace(
        generate_content=AsyncMock(return_value=resp)))
    return SimpleNamespace(aio=aio)


@pytest.mark.asyncio
async def test_annotate_pool_returns_parsed_ranked_output():
    parsed = _RankedOutput(courses=[
        _RankedItem(course_id="000211012", group="core",
                    reason_lead="總述",
                    reason_points=[{"term": "分析", "detail": "拆解"}]),
    ])
    client = _fake_client(parsed)
    candidates = [{"course_id": "000211012", "course_name": "政治學", "relevance": "x"}]
    out = await stage2_annotate_pool_async(client, "PM", ["分析"], candidates)
    assert out is parsed
    client.aio.models.generate_content.assert_awaited_once()
