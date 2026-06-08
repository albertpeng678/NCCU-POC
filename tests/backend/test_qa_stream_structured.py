# tests/backend/test_qa_stream_structured.py
"""stream_answer_structured：3.5 結構化串流——逐 chunk 從部分 JSON 增量抽 answer 值 yield token，
末尾 done 帶 grounding course_ids + 原始 JSON 全文。mock client.aio.models.generate_content_stream。"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from backend.qa import stream_answer_structured


def _chunk(text, course_id=None):
    # content=None → _visible_text_from_chunk 退回 chunk.text；grounding 用 title 的「課程代號:」regex 兜底
    if course_id:
        rc = SimpleNamespace(custom_metadata=None, title=f"課程代號: {course_id}", text="", uri=None)
        gm = SimpleNamespace(grounding_chunks=[SimpleNamespace(retrieved_context=rc)])
        cands = [SimpleNamespace(grounding_metadata=gm, content=None)]
    else:
        cands = [SimpleNamespace(grounding_metadata=None, content=None)]
    return SimpleNamespace(text=text, candidates=cands)


async def _aiter(items):
    for it in items:
        yield it


def _fake_client(chunks):
    aio = SimpleNamespace(models=SimpleNamespace(
        generate_content_stream=AsyncMock(return_value=_aiter(chunks))))
    return SimpleNamespace(aio=aio)


@pytest.mark.asyncio
async def test_structured_stream_yields_incremental_answer_then_done():
    # JSON 分段串流：token 只吐 answer 值（不含 JSON 鷹架）
    chunks = [
        _chunk('{"answer": "政治學'),
        _chunk(' 很棒",'),
        _chunk(' "followup_suggestions": []}', course_id="000211012"),
    ]
    client = _fake_client(chunks)
    events = [ev async for ev in stream_answer_structured(client, "store", "問政治學", None)]

    tokens = "".join(e["data"]["text"] for e in events if e["event"] == "token")
    assert tokens == "政治學 很棒"
    assert all('"answer"' not in e["data"]["text"] for e in events if e["event"] == "token")

    done = [e for e in events if e["event"] == "done"]
    assert len(done) == 1
    assert done[0]["data"]["course_ids"] == ["000211012"]
    assert '"answer"' in done[0]["data"]["answer_text"]   # 原始 JSON 全文留給 finalize/parse


@pytest.mark.asyncio
async def test_structured_stream_config_uses_schema_and_low_thinking():
    chunks = [_chunk('{"answer": "x", "followup_suggestions": []}')]
    client = _fake_client(chunks)
    _ = [ev async for ev in stream_answer_structured(client, "store", "q", None)]
    call = client.aio.models.generate_content_stream.await_args
    assert call.kwargs["model"] == "gemini-3.5-flash"
    cfg = call.kwargs["config"]
    assert cfg.response_schema is not None
    assert cfg.response_mime_type == "application/json"
    assert cfg.thinking_config is not None          # thinking_level=low 已設（加速且 grounding 不掉）
