# tests/backend/test_qa_stream_structured.py
"""stream_answer_structured：3.5 結構化串流——逐 chunk 從部分 JSON 增量抽 answer 值 yield token，
末尾 done 帶 grounding course_ids + 原始 JSON 全文。mock client.aio.models.generate_content_stream。"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from backend.qa import stream_answer_structured


def _chunk(text, course_id=None, use_custom_metadata=False):
    # content=None → _visible_text_from_chunk 退回 chunk.text
    # use_custom_metadata=True → 走 canonical custom_metadata 路徑（官方 grounding）
    # use_custom_metadata=False → 走 title 的「課程代號:」regex fallback
    if course_id:
        if use_custom_metadata:
            cm = [SimpleNamespace(key="course_id", string_value=course_id, numeric_value=None)]
            rc = SimpleNamespace(custom_metadata=cm, title="", text="", uri=None)
        else:
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
    # thinking_level 必須是 low（非 high/medium/minimal），相容 enum 和字串兩種形狀
    assert str(cfg.thinking_config.thinking_level).lower().endswith("low")


def test_answer_question_structured_sets_low_thinking(monkeypatch):
    from types import SimpleNamespace
    import backend.qa as qa
    captured = {}

    def fake_generate_content(model, contents, config):
        captured["config"] = config
        return SimpleNamespace(parsed={"answer": "x", "followup_suggestions": []},
                               text='{"answer":"x","followup_suggestions":[]}', candidates=[])

    client = SimpleNamespace(models=SimpleNamespace(generate_content=fake_generate_content))
    qa.answer_question_structured(client, "store", "q", history=None)
    # thinking_level 必須是 low（非 high/medium/minimal），相容 enum 和字串兩種形狀
    assert str(captured["config"].thinking_config.thinking_level).lower().endswith("low")


@pytest.mark.asyncio
async def test_custom_metadata_grounding_deduplicates_course_ids():
    """canonical custom_metadata 路徑：兩個 chunk 帶相同 course_id → done.course_ids 去重後單筆。

    真實流量走 retrieved_context.custom_metadata（list of SimpleNamespace(key, string_value, numeric_value)），
    非 title regex fallback。驗 _course_id_from_retrieved_context 能讀到並去重。
    """
    # 兩個 chunk 都帶 course_id=000211012（同一課的不同段落，真實 grounding 常見）
    chunks = [
        _chunk('{"answer": "政治', course_id="000211012", use_custom_metadata=True),
        _chunk('學 入門",', course_id="000211012", use_custom_metadata=True),
        _chunk(' "followup_suggestions": []}'),
    ]
    client = _fake_client(chunks)
    events = [ev async for ev in stream_answer_structured(client, "store", "問政治學", None)]

    done = [e for e in events if e["event"] == "done"]
    assert len(done) == 1
    # 去重後應為單筆，不得重複
    assert done[0]["data"]["course_ids"] == ["000211012"]
