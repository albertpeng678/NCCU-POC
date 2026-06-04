# tests/backend/test_qa_stream.py
"""stream_answer async generator：逐 chunk yield token，末尾 yield done（含 course_ids + 累積全文）。
mock client.aio.models.generate_content_stream 為回傳 async iterator 的 AsyncMock。"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from backend.qa import stream_answer


def _chunk(text, course_id=None):
    if course_id:
        rc = SimpleNamespace(title=f"課程代號: {course_id}", text="", uri=None)
        gm = SimpleNamespace(grounding_chunks=[SimpleNamespace(retrieved_context=rc)])
        cands = [SimpleNamespace(grounding_metadata=gm)]
    else:
        cands = [SimpleNamespace(grounding_metadata=None)]
    return SimpleNamespace(text=text, candidates=cands)


async def _aiter(items):
    for it in items:
        yield it


def _fake_stream_client(chunks):
    # generate_content_stream 是 async def 回傳 async iterator → AsyncMock，return_value 為 async gen
    aio = SimpleNamespace(models=SimpleNamespace(
        generate_content_stream=AsyncMock(return_value=_aiter(chunks))))
    return SimpleNamespace(aio=aio)


async def _collect(gen):
    return [ev async for ev in gen]


@pytest.mark.asyncio
async def test_stream_yields_tokens_then_done():
    chunks = [_chunk("政治學"), _chunk(" 很棒", course_id="000211012")]
    client = _fake_stream_client(chunks)
    events = await _collect(stream_answer(client, "store", "問政治學", history=None))

    tokens = [e["data"]["text"] for e in events if e["event"] == "token"]
    assert tokens == ["政治學", " 很棒"]

    done = [e for e in events if e["event"] == "done"]
    assert len(done) == 1
    assert done[0]["data"]["course_ids"] == ["000211012"]
    assert done[0]["data"]["answer_text"] == "政治學 很棒"
    client.aio.models.generate_content_stream.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_skips_empty_text_chunks():
    chunks = [_chunk(""), _chunk("有字"), _chunk(None)]
    client = _fake_stream_client(chunks)
    events = await _collect(stream_answer(client, "store", "q", history=None))
    tokens = [e for e in events if e["event"] == "token"]
    assert len(tokens) == 1
    assert tokens[0]["data"]["text"] == "有字"
