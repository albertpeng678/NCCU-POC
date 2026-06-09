# tests/backend/test_judge_openai.py
"""TDD tests for evaluate_recommendation using OpenAI responses.parse."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.judge import evaluate_recommendation


def _make_openai_client(parsed_obj):
    """Build a mock AsyncOpenAI client whose responses.parse returns parsed_obj."""
    client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.output_parsed = parsed_obj
    client.responses.parse = AsyncMock(return_value=mock_resp)
    return client


@pytest.mark.asyncio
async def test_evaluate_recommendation_returns_scores():
    """evaluate_recommendation returns correct score dict when OpenAI returns structured output."""
    from backend.judge import _JudgeOutput  # Pydantic model must exist after implementation

    parsed = _JudgeOutput(
        relevance=4,
        grouping=5,
        reason_quality=3,
        diversity=4,
        critique="整體不錯",
    )
    client = _make_openai_client(parsed)
    result = {
        "groups": {
            "core": [{"name": "行銷管理", "department": "企管系", "reason": "培養市場敏感度"}],
            "supporting": [],
            "extended": [],
        }
    }
    scores = await evaluate_recommendation(client, "產品經理", result)
    assert scores is not None
    assert scores["judge_relevance_score"] == 4
    assert scores["judge_grouping_score"] == 5
    assert scores["judge_reason_score"] == 3
    assert scores["judge_diversity_score"] == 4
    assert scores["judge_overall_score"] == 4  # round((4+5+3+4)/4)
    assert scores["judge_critique"] == "整體不錯"


@pytest.mark.asyncio
async def test_evaluate_recommendation_clamps_scores():
    """Scores are clamped to 1-5 range."""
    from backend.judge import _JudgeOutput

    parsed = _JudgeOutput(relevance=6, grouping=0, reason_quality=3, diversity=3, critique="x")
    client = _make_openai_client(parsed)
    scores = await evaluate_recommendation(client, "醫師", {"groups": {}})
    assert scores["judge_relevance_score"] == 5
    assert scores["judge_grouping_score"] == 1


@pytest.mark.asyncio
async def test_evaluate_recommendation_exception_returns_none():
    """When responses.parse raises, evaluate_recommendation returns None (graceful degradation)."""
    client = MagicMock()
    client.responses.parse = AsyncMock(side_effect=Exception("API error"))
    scores = await evaluate_recommendation(client, "護理師", {"groups": {}})
    assert scores is None


@pytest.mark.asyncio
async def test_evaluate_recommendation_none_parsed_returns_none():
    """When output_parsed is None (refusal/token-limit), returns None."""
    client = _make_openai_client(None)
    scores = await evaluate_recommendation(client, "律師", {"groups": {}})
    assert scores is None


@pytest.mark.asyncio
async def test_evaluate_recommendation_calls_responses_parse():
    """Verify that responses.parse is called (not generate_content)."""
    from backend.judge import _JudgeOutput

    parsed = _JudgeOutput(relevance=3, grouping=3, reason_quality=3, diversity=3, critique="ok")
    client = _make_openai_client(parsed)
    await evaluate_recommendation(client, "工程師", {"groups": {}})
    client.responses.parse.assert_called_once()
