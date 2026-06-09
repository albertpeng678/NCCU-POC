# tests/backend/test_qa_followups.py
"""generate_followups（兩段式後續問題建議）TDD 測試。

測試三個情境：
1. 正常：output_parsed.followups = ["A","B","C"] → 回該 3 個
2. output_parsed = None → 回 []（不傳播錯誤）
3. responses.parse 拋例外 → 回 []（不傳播錯誤）

另加一個 /qa/stream stream 分支整合測試：done event 帶 followup_suggestions（非空清單）。
"""
import pytest
import json
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

from backend.qa import generate_followups


# ─────────────────────────────── helpers ───────────────────────────────────


def _make_client(followups=None, parsed_none=False, raise_exc=None):
    """回傳一個 mock AsyncOpenAI-like client，responses.parse 依參數行動。"""

    async def _parse(**kwargs):
        if raise_exc is not None:
            raise raise_exc
        if parsed_none:
            return SimpleNamespace(output_parsed=None)
        return SimpleNamespace(
            output_parsed=SimpleNamespace(followups=followups or [])
        )

    return SimpleNamespace(responses=SimpleNamespace(parse=_parse))


# ─────────────────────────────── unit tests ────────────────────────────────


@pytest.mark.asyncio
async def test_generate_followups_returns_three_suggestions():
    """output_parsed.followups = ["A","B","C"] → 回該 3 個。"""
    client = _make_client(followups=["問題A", "問題B", "問題C"])
    result = await generate_followups(client, "有什麼課？", "政大有很多課。")
    assert result == ["問題A", "問題B", "問題C"]


@pytest.mark.asyncio
async def test_generate_followups_truncates_to_three():
    """output_parsed.followups 超過 3 個 → 只回前 3。"""
    client = _make_client(followups=["A", "B", "C", "D", "E"])
    result = await generate_followups(client, "q", "a")
    assert result == ["A", "B", "C"]


@pytest.mark.asyncio
async def test_generate_followups_returns_empty_on_none_parsed():
    """output_parsed = None → 回 []（不拋例外）。"""
    client = _make_client(parsed_none=True)
    result = await generate_followups(client, "q", "a")
    assert result == []


@pytest.mark.asyncio
async def test_generate_followups_returns_empty_on_exception():
    """responses.parse 拋例外 → 回 []（不傳播）。"""
    client = _make_client(raise_exc=RuntimeError("API error"))
    result = await generate_followups(client, "q", "a")
    assert result == []


@pytest.mark.asyncio
async def test_generate_followups_returns_empty_on_value_error():
    """structured output ValueError（refusal/token-limit）→ 回 []（不傳播）。"""
    client = _make_client(raise_exc=ValueError("refusal"))
    result = await generate_followups(client, "q", "a")
    assert result == []


# ─────────────────────────────── integration: /qa/stream stream branch ────


def _text_delta(delta: str):
    return SimpleNamespace(delta=delta)


def _output_item_done_file_search(results=None):
    item = SimpleNamespace(type="file_search_call", results=results or [])
    return SimpleNamespace(item=item)


def _result(course_id, score=0.9):
    return SimpleNamespace(
        attributes={"course_id": course_id},
        filename=f"{course_id}.txt",
        score=score,
    )


async def _aiter(items):
    for it in items:
        yield it


async def _gen_qa_with_followups():
    """stream_answer 回答含一筆 course_id，done 帶 answer_text。"""
    yield {"event": "token", "data": {"text": "政治學很好"}}
    yield {"event": "done", "data": {
        "course_ids": ["000211012"],
        "answer_text": "政治學**很好**",
    }}


@pytest.mark.asyncio
async def test_qa_stream_done_event_has_followup_suggestions():
    """stream 分支 /qa/stream：done event 的 followup_suggestions 來自 generate_followups。"""
    from fastapi.testclient import TestClient

    meta = {
        "000211012": {
            "name": "政治學",
            "department": "政治系",
            "teacher": "蔡",
            "credits": 3.0,
            "syllabus_url": "https://x/a",
        }
    }

    def _events(text):
        out = []
        cur_event = None
        for line in text.splitlines():
            if line.startswith("event:"):
                cur_event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                out.append((cur_event, line[len("data:"):].strip()))
        return out

    mock_followups = ["想修政治學需要什麼先修？", "政治學老師怎樣？", "還有哪些相關課程？"]

    with patch("backend.main._QA_MODE", "stream"), \
         patch("backend.main.stream_answer", return_value=_gen_qa_with_followups()), \
         patch("backend.main.generate_followups",
               new=AsyncMock(return_value=mock_followups)), \
         patch("backend.main.get_pool", return_value=object()), \
         patch("backend.main.create_session", new=AsyncMock(return_value="sess-f1")), \
         patch("backend.main.get_session_turns", new=AsyncMock(return_value=[])), \
         patch("backend.main.insert_turn", new=AsyncMock(return_value=1)), \
         patch("backend.main.bump_session", new=AsyncMock(return_value=None)), \
         patch("backend.main.load_courses_meta", return_value=meta):
        from backend.main import app
        client = TestClient(app)
        resp = client.get("/qa/stream?question=介紹政治學")

    assert resp.status_code == 200
    evs = _events(resp.text)
    done_data_str = [d for e, d in evs if e == "done"]
    assert done_data_str, "should have done event"
    done_data = json.loads(done_data_str[0])
    assert "followup_suggestions" in done_data
    assert done_data["followup_suggestions"] == mock_followups
