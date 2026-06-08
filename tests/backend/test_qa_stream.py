# tests/backend/test_qa_stream.py
"""stream_answer（OpenAI Responses API）：逐 token yield，末尾 yield done（含 course_ids + 累積全文）。

Phase 2 注：stream_answer 已從 Gemini generate_content_stream 切至 OpenAI Responses API。
mock 改為 client.responses.create 回傳 async iterable。
"""
import pytest
from types import SimpleNamespace

from backend.qa import stream_answer


def _text_delta(delta: str):
    """模擬 ResponseTextDeltaEvent（duck-type：有 .delta str 屬性）"""
    return SimpleNamespace(delta=delta)


def _output_item_done_file_search(results=None):
    """模擬 ResponseOutputItemDoneEvent (file_search_call)"""
    item = SimpleNamespace(type="file_search_call", results=results or [])
    return SimpleNamespace(item=item)


def _other_event():
    """模擬任何其他事件（無 .delta、無有效 .item）"""
    return SimpleNamespace()


def _result(course_id, score=0.9):
    return SimpleNamespace(
        attributes={"course_id": course_id},
        filename=f"{course_id}.txt",
        score=score,
    )


async def _aiter(items):
    for it in items:
        yield it


class _AsyncStreamCM:
    """Mirrors OpenAI AsyncStream: supports async with and async for."""

    def __init__(self, items):
        self._items = items

    def __aiter__(self):
        return _aiter(self._items).__aiter__()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _fake_openai_client(events):
    async def _create(**kwargs):
        return _AsyncStreamCM(events)
    return SimpleNamespace(responses=SimpleNamespace(create=_create))


async def _collect(gen):
    return [ev async for ev in gen]


def _joined_tokens(events):
    return "".join(e["data"]["text"] for e in events if e["event"] == "token")


@pytest.mark.asyncio
async def test_stream_yields_tokens_then_done():
    events = [
        _other_event(),  # e.g. file_search in_progress
        _output_item_done_file_search([_result("000211012")]),
        _text_delta("政治學"),
        _text_delta(" 很棒"),
        _other_event(),  # message done
    ]
    client = _fake_openai_client(events)
    result = await _collect(stream_answer(client, "vs_123", "問政治學", history=None))

    assert _joined_tokens(result) == "政治學 很棒"

    done = [e for e in result if e["event"] == "done"]
    assert len(done) == 1
    assert done[0]["data"]["course_ids"] == ["000211012"]
    assert done[0]["data"]["answer_text"] == "政治學 很棒"


@pytest.mark.asyncio
async def test_stream_skips_empty_text_chunks():
    events = [
        _text_delta(""),   # empty delta → skip
        _text_delta("有字"),
        _text_delta(""),   # empty delta → skip
    ]
    client = _fake_openai_client(events)
    result = await _collect(stream_answer(client, "vs_123", "q", history=None))
    assert _joined_tokens(result) == "有字"


@pytest.mark.asyncio
async def test_stream_raw_markdown_passed_through():
    """OpenAI 不做 JSON 包裝 → markdown 原文（含表格、粗體）直接透傳，不做 fence 截斷。"""
    md = "這是答案說明。\n\n| 課程 | 系所 |\n| --- | --- |\n| A | B |\n"
    events = [
        _output_item_done_file_search([_result("000211012")]),
        _text_delta("這是答案說明。\n\n"),
        _text_delta("| 課程 | 系所 |\n| --- | --- |\n| A | B |\n"),
    ]
    client = _fake_openai_client(events)
    result = await _collect(stream_answer(client, "vs_123", "q", history=None))

    streamed = _joined_tokens(result)
    # 全部 markdown 直接串給前端（無 fence 截斷、無 JSON 包裝）
    assert "這是答案說明。" in streamed
    assert "| 課程 | 系所 |" in streamed

    done = [e for e in result if e["event"] == "done"][0]
    assert done["data"]["course_ids"] == ["000211012"]
    assert "這是答案說明。" in done["data"]["answer_text"]
