# tests/backend/test_qa_stream_openai.py
"""stream_answer（OpenAI Responses API）：
- ResponseTextDeltaEvent → yield token
- ResponseOutputItemDoneEvent (file_search_call) → collect course_ids via results
- done event carries course_ids + answer_text
- 空 results → NO_RESULTS_MESSAGE（out-of-scope 守衛由 finalize_qa_answer 處理）
- multi-turn history 正確組成 input 清單
"""
import pytest
from types import SimpleNamespace

from backend.qa import stream_answer, NO_RESULTS_MESSAGE


# ─────────────────────────────── helpers ────────────────────────────────────

def _text_delta(delta: str):
    """模擬 ResponseTextDeltaEvent"""
    return SimpleNamespace(
        __class__=type("ResponseTextDeltaEvent", (), {}),
        delta=delta,
    )


def _output_item_done(item_type: str, results=None):
    """模擬 ResponseOutputItemDoneEvent"""
    if item_type == "file_search_call":
        item = SimpleNamespace(type="file_search_call", results=results or [])
    else:
        item = SimpleNamespace(type=item_type)
    return SimpleNamespace(
        __class__=type("ResponseOutputItemDoneEvent", (), {}),
        item=item,
    )


def _other_event(name: str):
    """模擬任何其他事件（file_search in_progress/searching/completed 等）"""
    return SimpleNamespace(__class__=type(name, (), {}))


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
    """Wraps an async iterable as an async context manager (mirrors OpenAI AsyncStream)."""

    def __init__(self, items):
        self._items = items

    def __aiter__(self):
        return _aiter(self._items).__aiter__()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _fake_openai_client(events):
    """回傳一個 AsyncOpenAI-like 物件，responses.create 回 async context manager + iterable。"""
    async def _create(**kwargs):
        return _AsyncStreamCM(events)

    responses = SimpleNamespace(create=_create)
    return SimpleNamespace(responses=responses)


async def _collect(gen):
    return [ev async for ev in gen]


# ─────────────────────────────── tests ──────────────────────────────────────

@pytest.mark.asyncio
async def test_stream_yields_text_delta_tokens():
    """ResponseTextDeltaEvent.delta → yield token event。"""
    events = [
        _other_event("ResponseCreatedEvent"),
        _other_event("ResponseFileSearchCallInProgressEvent"),
        _other_event("ResponseFileSearchCallSearchingEvent"),
        _other_event("ResponseFileSearchCallCompletedEvent"),
        _output_item_done("file_search_call", [_result("703850001"), _result("090109001")]),
        _text_delta("政大有"),
        _text_delta("幾門"),
        _text_delta("課程。"),
        _output_item_done("message"),
        _other_event("ResponseCompletedEvent"),
    ]
    client = _fake_openai_client(events)
    result = await _collect(stream_answer(client, "vs_123", "有什麼課？"))

    tokens = [e for e in result if e["event"] == "token"]
    joined = "".join(t["data"]["text"] for t in tokens)
    assert joined == "政大有幾門課程。"

    done = [e for e in result if e["event"] == "done"]
    assert len(done) == 1
    assert done[0]["data"]["answer_text"] == "政大有幾門課程。"


@pytest.mark.asyncio
async def test_stream_collects_course_ids_from_results():
    """OutputItemDone file_search_call.results → done.course_ids"""
    events = [
        _output_item_done("file_search_call", [
            _result("703850001"),
            _result("090109001"),
            _result("754013001"),
        ]),
        _text_delta("答案"),
        _output_item_done("message"),
    ]
    client = _fake_openai_client(events)
    result = await _collect(stream_answer(client, "vs_123", "問"))

    done = [e for e in result if e["event"] == "done"][0]
    assert done["data"]["course_ids"] == ["703850001", "090109001", "754013001"]


@pytest.mark.asyncio
async def test_stream_empty_results_returns_no_match_course_ids():
    """results 為空 → done.course_ids 為空清單（no_match 判斷由 finalize_qa_answer 負責）。"""
    events = [
        _output_item_done("file_search_call", []),  # 空 results
        _text_delta("我沒有找到相關課程"),
        _output_item_done("message"),
    ]
    client = _fake_openai_client(events)
    result = await _collect(stream_answer(client, "vs_123", "問"))

    done = [e for e in result if e["event"] == "done"][0]
    assert done["data"]["course_ids"] == []


@pytest.mark.asyncio
async def test_stream_skips_non_delta_events():
    """非 ResponseTextDeltaEvent 不 yield token。"""
    events = [
        _other_event("ResponseCreatedEvent"),
        _other_event("ResponseFileSearchCallInProgressEvent"),
        _text_delta("有效"),
        _other_event("ResponseTextDoneEvent"),
        _other_event("ResponseCompletedEvent"),
    ]
    client = _fake_openai_client(events)
    result = await _collect(stream_answer(client, "vs_123", "問"))

    tokens = [e for e in result if e["event"] == "token"]
    assert len(tokens) == 1
    assert tokens[0]["data"]["text"] == "有效"


@pytest.mark.asyncio
async def test_stream_deduplicates_course_ids():
    """同一 course_id 兩個 results → done.course_ids 去重。"""
    events = [
        _output_item_done("file_search_call", [
            _result("703850001"),
            _result("703850001"),  # duplicate
            _result("090109001"),
        ]),
        _text_delta("x"),
    ]
    client = _fake_openai_client(events)
    result = await _collect(stream_answer(client, "vs_123", "問"))

    done = [e for e in result if e["event"] == "done"][0]
    assert done["data"]["course_ids"] == ["703850001", "090109001"]


@pytest.mark.asyncio
async def test_stream_multiturn_history_included_in_input():
    """多輪歷史 → responses.create 的 input 包含歷史 user/assistant 輪次。"""
    captured = {}

    async def _create(**kwargs):
        captured.update(kwargs)
        return _AsyncStreamCM([_text_delta("ok"), _output_item_done("message")])

    client = SimpleNamespace(responses=SimpleNamespace(create=_create))

    history = [
        {"question": "前一輪問題", "answer": "前一輪答案"},
    ]
    await _collect(stream_answer(client, "vs_123", "本輪問題", history=history))

    inp = captured.get("input", [])
    # system 在前，歷史兩輪 (user+assistant)，最後是本輪 user
    roles = [m.get("role") for m in inp if isinstance(m, dict)]
    assert roles[0] == "system"
    # history turn
    assert any(m.get("role") == "user" and "前一輪問題" in (m.get("content") or "") for m in inp)
    assert any(m.get("role") == "assistant" and "前一輪答案" in (m.get("content") or "") for m in inp)
    # current question last
    assert inp[-1]["role"] == "user"
    assert "本輪問題" in inp[-1]["content"]


@pytest.mark.asyncio
async def test_stream_no_thought_leakage():
    """OpenAI 不需 thought 過濾——token 逐字透傳，不做任何 thought 剝除。"""
    events = [
        _text_delta("直接透傳"),
        _text_delta("，不過濾"),
    ]
    client = _fake_openai_client(events)
    result = await _collect(stream_answer(client, "vs_123", "問"))

    joined = "".join(e["data"]["text"] for e in result if e["event"] == "token")
    assert joined == "直接透傳，不過濾"
