# tests/backend/test_qa_judge_openai.py
"""TDD tests for evaluate_qa using OpenAI responses.parse."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.qa_judge import evaluate_qa


def _make_openai_client(parsed_obj):
    """Build a mock AsyncOpenAI client whose responses.parse returns parsed_obj."""
    client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.output_parsed = parsed_obj
    client.responses.parse = AsyncMock(return_value=mock_resp)
    return client


@pytest.mark.asyncio
async def test_evaluate_qa_returns_scores():
    """evaluate_qa returns correct score dict when OpenAI returns structured output."""
    from backend.qa_judge import _QaJudgeOutput  # Pydantic model must exist after implementation

    parsed = _QaJudgeOutput(
        faithfulness=4,
        relevancy=5,
        context_precision=3,
        critique="答案忠於課綱",
    )
    client = _make_openai_client(parsed)
    scores = await evaluate_qa(client, "哪些課教Python?", "課程A教Python", ["政治學"])
    assert scores is not None
    assert scores["judge_faithfulness"] == 4
    assert scores["judge_relevancy"] == 5
    assert scores["judge_context_prec"] == 3
    assert scores["judge_overall"] == 4  # round((4+5+3)/3)
    assert scores["judge_critique"] == "答案忠於課綱"


@pytest.mark.asyncio
async def test_evaluate_qa_clamps_scores():
    """Scores are clamped to 1-5 range."""
    from backend.qa_judge import _QaJudgeOutput

    parsed = _QaJudgeOutput(faithfulness=9, relevancy=0, context_precision=3, critique="x")
    client = _make_openai_client(parsed)
    scores = await evaluate_qa(client, "q", "a", [])
    assert scores["judge_faithfulness"] == 5
    assert scores["judge_relevancy"] == 1


@pytest.mark.asyncio
async def test_evaluate_qa_exception_returns_none():
    """When responses.parse raises, evaluate_qa returns None (graceful degradation)."""
    client = MagicMock()
    client.responses.parse = AsyncMock(side_effect=Exception("API error"))
    scores = await evaluate_qa(client, "q", "a", [])
    assert scores is None


@pytest.mark.asyncio
async def test_evaluate_qa_none_parsed_returns_none():
    """When output_parsed is None (refusal/token-limit), returns None."""
    client = _make_openai_client(None)
    scores = await evaluate_qa(client, "q", "a", [])
    assert scores is None


@pytest.mark.asyncio
async def test_evaluate_qa_calls_responses_parse():
    """Verify that responses.parse is called (not generate_content)."""
    from backend.qa_judge import _QaJudgeOutput

    parsed = _QaJudgeOutput(faithfulness=3, relevancy=3, context_precision=3, critique="ok")
    client = _make_openai_client(parsed)
    await evaluate_qa(client, "question", "answer", ["course1"])
    client.responses.parse.assert_called_once()
