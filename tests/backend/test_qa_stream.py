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
    aio = SimpleNamespace(models=SimpleNamespace(
        generate_content_stream=AsyncMock(return_value=_aiter(chunks))))
    return SimpleNamespace(aio=aio)


async def _collect(gen):
    return [ev async for ev in gen]


def _joined_tokens(events):
    return "".join(e["data"]["text"] for e in events if e["event"] == "token")


@pytest.mark.asyncio
async def test_stream_yields_tokens_then_done():
    chunks = [_chunk("政治學"), _chunk(" 很棒", course_id="000211012")]
    client = _fake_stream_client(chunks)
    events = await _collect(stream_answer(client, "store", "問政治學", history=None))

    # token 可能被重新分塊（緩衝），驗串接後全文
    assert _joined_tokens(events) == "政治學 很棒"

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
    assert _joined_tokens(events) == "有字"


@pytest.mark.asyncio
async def test_stream_stops_tokens_at_json_fence():
    """串流到 ```json 包裝就停止吐 token（不串 JSON 給使用者），但 answer_text 仍含全文供解析。"""
    chunks = [
        _chunk("這是答案說明。\n\n"),
        _chunk("| 課程 | 系所 |\n| --- | --- |\n| A | B |\n\n"),
        _chunk('```json\n{"answer": "這是答案說明。", "followup_suggestions": []}\n```',
               course_id="000211012"),
    ]
    client = _fake_stream_client(chunks)
    events = await _collect(stream_answer(client, "store", "q", history=None))

    streamed = _joined_tokens(events)
    # 串給前端的不含 JSON 包裝
    assert "```json" not in streamed
    assert '"answer"' not in streamed
    # 但乾淨 prose + 表格有串出去
    assert "這是答案說明" in streamed
    assert "| 課程 | 系所 |" in streamed
    # done 的 answer_text 仍是完整原文（供 parse_qa_response 解析）
    done = [e for e in events if e["event"] == "done"][0]
    assert "```json" in done["data"]["answer_text"]
    assert done["data"]["course_ids"] == ["000211012"]
