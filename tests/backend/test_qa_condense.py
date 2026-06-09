# tests/backend/test_qa_condense.py
"""修 B：condense_question（condense-then-search 多輪）TDD。

(a) mock responses.parse → 斷言 condense_question 回改寫 query
(b) history 空 → 回原 question
(c) 例外 → 回原 question（不可拖垮問答）
(d) main.py 接線：mock condense 回改寫 query，
    斷言 stream_answer 收到改寫 query，insert_turn 收到原 question。
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from types import SimpleNamespace

from backend.qa import condense_question


# ─────────────────────────────────── helpers ────────────────────────────────


def _make_client(parsed_query: str | None, raise_exc=None):
    """回傳一個 AsyncOpenAI-like 物件，responses.parse 回 parsed_query 或拋例外。"""
    if raise_exc is not None:
        async def _parse(**kwargs):
            raise raise_exc

        parse_mock = _parse
    else:
        result = SimpleNamespace(output_parsed=SimpleNamespace(query=parsed_query))

        async def _parse(**kwargs):
            return result

        parse_mock = _parse

    return SimpleNamespace(responses=SimpleNamespace(parse=_parse_mock(parse_mock)))


def _parse_mock(fn):
    """回傳一個 callable，呼叫時把所有 kwargs 傳給 fn。"""
    async def _call(*args, **kwargs):
        return await fn(*args, **kwargs)
    return _call


# ────────────────────────────── (a) 改寫 query ──────────────────────────────


@pytest.mark.asyncio
async def test_condense_question_returns_rewritten_query():
    """history 非空 + mock parse 回 standalone query → condense 回改寫後 query。"""
    parsed = SimpleNamespace(output_parsed=SimpleNamespace(query="想往金融領域發展的PM要修什麼課"))

    async def _fake_parse(**kwargs):
        return parsed

    client = SimpleNamespace(responses=SimpleNamespace(parse=_fake_parse))

    history = [{"question": "想成為PM要修什麼課", "answer": "PM需要產品策略、數據分析…"}]
    result = await condense_question(client, "那金融呢", history)
    assert result == "想往金融領域發展的PM要修什麼課"


# ──────────────────────────── (b) history 空 ────────────────────────────────


@pytest.mark.asyncio
async def test_condense_question_empty_history_returns_original():
    """history 空 → 不呼叫 LLM，直接回原 question。"""
    called = []

    async def _should_not_be_called(**kwargs):
        called.append(True)
        return SimpleNamespace(output_parsed=SimpleNamespace(query="x"))

    client = SimpleNamespace(responses=SimpleNamespace(parse=_should_not_be_called))

    result = await condense_question(client, "那金融呢", [])
    assert result == "那金融呢"
    assert not called, "history 空時不應呼叫 LLM"


@pytest.mark.asyncio
async def test_condense_question_none_history_returns_original():
    """history=None → 不呼叫 LLM，直接回原 question。"""
    called = []

    async def _should_not_be_called(**kwargs):
        called.append(True)
        return SimpleNamespace(output_parsed=SimpleNamespace(query="x"))

    client = SimpleNamespace(responses=SimpleNamespace(parse=_should_not_be_called))

    result = await condense_question(client, "問題", None)
    assert result == "問題"
    assert not called


# ──────────────────────────── (c) 例外保底 ──────────────────────────────────


@pytest.mark.asyncio
async def test_condense_question_exception_returns_original():
    """LLM 拋例外 → 靜默 fallback 回原 question，不讓問答崩潰。"""
    async def _raise(**kwargs):
        raise RuntimeError("模擬 API 掛掉")

    client = SimpleNamespace(responses=SimpleNamespace(parse=_raise))

    history = [{"question": "想成為PM要修什麼課", "answer": "PM需要…"}]
    result = await condense_question(client, "那金融呢", history)
    assert result == "那金融呢", "例外時應 fallback 回原 question"


@pytest.mark.asyncio
async def test_condense_question_none_parsed_returns_original():
    """output_parsed 為 None → fallback 回原 question。"""
    result_obj = SimpleNamespace(output_parsed=None)

    async def _fake_parse(**kwargs):
        return result_obj

    client = SimpleNamespace(responses=SimpleNamespace(parse=_fake_parse))

    history = [{"question": "q", "answer": "a"}]
    result = await condense_question(client, "追問", history)
    assert result == "追問"


@pytest.mark.asyncio
async def test_condense_question_empty_query_returns_original():
    """output_parsed.query 為空字串 → fallback 回原 question。"""
    result_obj = SimpleNamespace(output_parsed=SimpleNamespace(query=""))

    async def _fake_parse(**kwargs):
        return result_obj

    client = SimpleNamespace(responses=SimpleNamespace(parse=_fake_parse))

    history = [{"question": "q", "answer": "a"}]
    result = await condense_question(client, "追問", history)
    assert result == "追問"


# ───── (d) main.py 接線：condense query 用於 stream_answer，原 question 用於 insert_turn ─────


async def _fake_stream_done(client, vs_id, question, history=None):
    yield {"event": "token", "data": {"text": "結果"}}
    yield {"event": "done", "data": {
        "course_ids": ["000211012"],
        "answer_text": "**政治學**很重要。",
    }}


@pytest.fixture
def tc():
    from backend.main import app
    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_store():
    from backend import qa_logger
    qa_logger.reset_ephemeral_store()
    yield
    qa_logger.reset_ephemeral_store()


def test_qa_stream_uses_condensed_query_for_search_but_stores_original(tc):
    """condense 回改寫 query → stream_answer 收到改寫 query；insert_turn 存原 question。

    驗證接線正確：改寫 query 只影響檢索，不影響歷史記錄。
    """
    from backend.qa import _StandaloneQuery

    condensed = "想往金融領域發展的PM要修什麼課"
    # 捕捉 stream_answer 收到的 question
    captured_stream_q = []
    # 捕捉 insert_turn 收到的 question
    captured_insert_q = []

    async def _fake_stream(client, vs_id, question, history=None):
        captured_stream_q.append(question)
        yield {"event": "token", "data": {"text": "ok"}}
        yield {"event": "done", "data": {
            "course_ids": [],
            "answer_text": "**課程**說明。",
        }}

    async def _fake_insert(pool, sid, tn, question, result, error):
        captured_insert_q.append(question)
        return "turn-1"

    async def _fake_condense(client, question, history):
        return condensed

    meta = {"000211012": {"name": "政治學", "department": "政治系",
                          "teacher": "x", "credits": 3.0, "syllabus_url": "#"}}

    with patch("backend.main._QA_MODE", "stream"),          patch("backend.main.get_pool", return_value=None),          patch("backend.main.stream_answer", _fake_stream),          patch("backend.main.insert_turn", side_effect=_fake_insert),          patch("backend.main.condense_question", side_effect=_fake_condense),          patch("backend.main.load_courses_meta", return_value=meta),          patch("backend.main.get_session_turns", new=AsyncMock(return_value=[
             {"question": "想成為PM", "answer": "PM要修…", "success": True}
         ])),          patch("backend.main.get_session", new=AsyncMock(return_value={"turn_count": 1})):
        # 第一輪建立 session
        r1 = tc.get("/qa/stream?question=那金融呢&session_id=fake-session")

    assert r1.status_code == 200
    # stream_answer 收到的是改寫後 query
    assert captured_stream_q, "stream_answer 應被呼叫"
    assert captured_stream_q[0] == condensed, \
        f"stream_answer 應收到改寫 query {condensed!r}，實際收到 {captured_stream_q[0]!r}"
    # insert_turn 存原始 question
    assert captured_insert_q, "insert_turn 應被呼叫"
    assert captured_insert_q[0] == "那金融呢", \
        f"insert_turn 應存原 question，實際存 {captured_insert_q[0]!r}"
