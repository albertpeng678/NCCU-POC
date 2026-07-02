# tests/backend/test_qa_dept_filter.py
"""系所感知受控檢索分支（Task 7）：backend.qa.stream_answer 整合測試。

全部用 mock（正式 vector store 尚未 backfill dept_canonical/degree_level attributes，
見 CLAUDE.md HANDOFF）：
- mock backend.dept_query.extract_slots 控制是否偵測到系所/學院/學制條件。
- mock client.vector_stores.search（async-iterable，鏡射 tests/backend/test_retrieval_openai.py
  的既有 mock 慣例）控制受控檢索的結果。
- mock client.responses.create（async context manager + async iterable，鏡射
  tests/backend/test_qa_stream_openai.py 的既有 mock 慣例）控制生成串流。

三個情境（見 task-7-brief.md）：
1. 指定系所 → build_dept_filter 產生正確 filter dict，且受控檢索分支被走到（filters 有帶入、
   不掛 file_search tool、答案用注入的 context 生成）。
2. 模糊查詢（extract_slots 全 None）→ build_dept_filter 回 None → 完全不呼叫
   vector_stores.search，走既有 file_search tool 路徑（行為不變）。
3. filter 命中但檢索空、且為多條件(and) → 放寬只留 dept_canonical 再試一次 → 仍空 →
   放棄過濾、回退純語意（既有 file_search tool 路徑），不拋錯、不 0 筆卡死。

tests/conftest.py 有一個 autouse fixture 把 backend.dept_query.extract_slots 預設鎖成全
None（避免其他既有測試意外觸發真實 OpenAI 呼叫）；本檔案的每個系所相關測試會自行
monkeypatch 覆寫掉這個預設值。
"""
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

from backend import dept_query
from backend.qa import stream_answer, retrieve_dept_filtered_context


# ── helpers（鏡射既有 test_qa_stream_openai.py / test_retrieval_openai.py 慣例）──────────────

def _text_delta(delta: str):
    return SimpleNamespace(delta=delta)


def _output_item_done_file_search(results=None):
    item = SimpleNamespace(type="file_search_call", results=results or [])
    return SimpleNamespace(item=item)


def _other_event():
    return SimpleNamespace()


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


class _AsyncIter:
    """Wraps a plain list as an async iterable (mirrors AsyncPaginator search results)."""

    def __init__(self, items):
        self._items = items

    def __aiter__(self):
        async def gen():
            for it in self._items:
                yield it
        return gen()


def _search_result(course_id: str, text: str = "課程內容"):
    return SimpleNamespace(
        attributes={"course_id": course_id},
        filename=f"{course_id}.txt",
        content=[SimpleNamespace(text=text)],
        score=0.9,
    )


def _make_client(create_events, search_side_effect=None):
    """回傳一個同時有 .responses.create（生成串流）與 .vector_stores.search（受控檢索）的 fake client。

    search_side_effect：list，每次呼叫 vector_stores.search 依序回傳一個 _AsyncIter(items)。
    未指定 → vector_stores.search 直接 assert 不該被呼叫（MagicMock 無 side_effect 但測試會用
    assert_not_called 驗證）。
    """
    async def _create(**kwargs):
        _create.calls.append(kwargs)
        return _AsyncStreamCM(create_events)
    _create.calls = []

    search_mock = MagicMock()
    if search_side_effect is not None:
        search_mock.side_effect = search_side_effect

    return SimpleNamespace(
        responses=SimpleNamespace(create=_create),
        vector_stores=SimpleNamespace(search=search_mock),
    ), search_mock


async def _collect(gen):
    return [ev async for ev in gen]


def _joined_tokens(events):
    return "".join(e["data"]["text"] for e in events if e["event"] == "token")


def _fake_extract_slots(department=None, college=None, degree_level=None):
    async def _fn(query):
        return {"department": department, "college": college, "degree_level": degree_level}
    return _fn


# ── 情境 1：指定系所 → 受控檢索分支被走到 ─────────────────────────────────────

@pytest.mark.asyncio
async def test_dept_filter_triggers_controlled_retrieval_branch(monkeypatch):
    """extract_slots 回歷史學系 → build_dept_filter 產生正確 filter；
    vector_stores.search 被呼叫且帶入該 filter；不掛 file_search tool；答案用注入 context 生成。
    """
    monkeypatch.setattr(dept_query, "extract_slots", _fake_extract_slots(department="歷史學系"))

    search_results = [_search_result("071001001", "課程名稱：中國近代史　開課系所：歷史學系")]
    client, search_mock = _make_client(
        create_events=[_text_delta("歷史系的課有"), _text_delta("中國近代史")],
        search_side_effect=[_AsyncIter(search_results)],
    )

    result = await _collect(stream_answer(client, "vs_123", "歷史系的課"))

    # 檢索有帶 filters（等同 build_dept_filter 對「department=歷史學系, 無學制」的輸出：
    # eq dept_canonical + ne degree_level 通識 的 and 組合）
    assert search_mock.call_count == 1
    call_kwargs = search_mock.call_args.kwargs
    assert call_kwargs["filters"] == dept_query.build_dept_filter(
        {"department": "歷史學系", "college": None, "degree_level": None}
    )
    assert call_kwargs["vector_store_id"] == "vs_123"

    # 生成呼叫沒有掛 file_search tool（context 已由後端注入）
    assert len(client.responses.create.calls) == 1
    gen_kwargs = client.responses.create.calls[0]
    assert "tools" not in gen_kwargs

    # 答案正常串出
    assert _joined_tokens(result) == "歷史系的課有中國近代史"

    done = [e for e in result if e["event"] == "done"][0]
    assert done["data"]["course_ids"] == ["071001001"]


@pytest.mark.asyncio
async def test_dept_filter_injects_retrieved_context_into_prompt(monkeypatch):
    """context_text 有帶進生成的 input（course_id + 課程內容），且系統指示有追加系所篩選補充說明
    （不改寫 _SYSTEM_INSTRUCTION 本體，只是接在後面）。
    """
    import backend.qa as qa_mod
    from backend.qa import _SYSTEM_INSTRUCTION

    async def _fake_retrieve(client, vs_id, query):
        return ("【課程代號：071001001】\n課程名稱：中國近代史", ["071001001"])

    monkeypatch.setattr(qa_mod, "retrieve_dept_filtered_context", _fake_retrieve)

    client, _ = _make_client(create_events=[_text_delta("答案")])
    await _collect(stream_answer(client, "vs_123", "歷史系的課"))

    gen_kwargs = client.responses.create.calls[0]
    system_msg = next(m for m in gen_kwargs["input"] if m["role"] == "system")
    user_msg = next(m for m in gen_kwargs["input"] if m["role"] == "user")

    # 系統指示：本體鐵則段仍在（沒被改寫），且有追加系所篩選補充說明
    assert "【鐵則・最高優先】" in system_msg["content"]
    assert system_msg["content"].startswith(_SYSTEM_INSTRUCTION)
    assert "系所篩選" in system_msg["content"]

    # user 訊息帶原問題 + 已檢索課程資料
    assert "歷史系的課" in user_msg["content"]
    assert "中國近代史" in user_msg["content"]


# ── 情境 2：模糊查詢 → 完全不呼叫受控檢索，走既有路徑 ──────────────────────────

@pytest.mark.asyncio
async def test_vague_query_skips_dept_filter_uses_existing_path(monkeypatch):
    """extract_slots 全 None → build_dept_filter None → vector_stores.search 完全不被呼叫；
    生成呼叫掛回既有 file_search tool（行為與改動前一致）。
    """
    monkeypatch.setattr(dept_query, "extract_slots", _fake_extract_slots())

    client, search_mock = _make_client(
        create_events=[
            _output_item_done_file_search([_search_result("000211012")]),
            _text_delta("有趣的課有很多"),
        ],
    )

    result = await _collect(stream_answer(client, "vs_123", "有什麼有趣的課"))

    search_mock.assert_not_called()

    gen_kwargs = client.responses.create.calls[0]
    assert gen_kwargs.get("tools") == [
        {
            "type": "file_search",
            "vector_store_ids": ["vs_123"],
            "max_num_results": 5,
            "ranking_options": {"score_threshold": 0.0},
        }
    ]
    assert _joined_tokens(result) == "有趣的課有很多"
    done = [e for e in result if e["event"] == "done"][0]
    assert done["data"]["course_ids"] == ["000211012"]


# ── 情境 3：filter 命中但檢索空 → 放寬 → 仍空 → 回退純語意 ─────────────────────

@pytest.mark.asyncio
async def test_empty_filtered_results_relaxes_then_falls_back(monkeypatch):
    """filter 為多條件(and)、第一次檢索空 → 放寬只留 dept_canonical 再試一次 → 仍空 →
    retrieve_dept_filtered_context 回 None → stream_answer 落回既有 file_search 路徑，
    不拋錯、不會卡在 0 筆。
    """
    monkeypatch.setattr(dept_query, "extract_slots", _fake_extract_slots(department="歷史學系"))

    client, search_mock = _make_client(
        create_events=[
            _output_item_done_file_search([_search_result("000211012")]),
            _text_delta("退回純語意的答案"),
        ],
        search_side_effect=[_AsyncIter([]), _AsyncIter([])],  # 第一次(and)空、放寬後仍空
    )

    result = await _collect(stream_answer(client, "vs_123", "歷史系的課"))

    # 兩次檢索呼叫：原始 filter + 放寬後的 dept-only filter
    assert search_mock.call_count == 2
    first_filters = search_mock.call_args_list[0].kwargs["filters"]
    second_filters = search_mock.call_args_list[1].kwargs["filters"]
    assert first_filters["type"] == "and"
    assert second_filters == {"type": "eq", "key": "dept_canonical", "value": "歷史學系"}

    # 落回既有路徑：一次生成呼叫、掛 file_search tool
    assert len(client.responses.create.calls) == 1
    assert "tools" in client.responses.create.calls[0]

    assert _joined_tokens(result) == "退回純語意的答案"
    done = [e for e in result if e["event"] == "done"][0]
    assert done["data"]["course_ids"] == ["000211012"]


@pytest.mark.asyncio
async def test_extract_slots_exception_falls_back_to_existing_path(monkeypatch):
    """extract_slots 拋例外（fail-open）→ retrieve_dept_filtered_context 回 None → 既有路徑。"""
    async def _boom(query):
        raise RuntimeError("模擬 API 掛掉")
    monkeypatch.setattr(dept_query, "extract_slots", _boom)

    client, search_mock = _make_client(
        create_events=[_text_delta("正常答案")],
    )

    result = await _collect(stream_answer(client, "vs_123", "歷史系的課"))

    search_mock.assert_not_called()
    assert "tools" in client.responses.create.calls[0]
    assert _joined_tokens(result) == "正常答案"


# ── retrieve_dept_filtered_context 直接單元測試（輔助 debug、行為更精準）──────────

@pytest.mark.asyncio
async def test_retrieve_dept_filtered_context_returns_none_when_no_filter(monkeypatch):
    monkeypatch.setattr(dept_query, "extract_slots", _fake_extract_slots())
    client, search_mock = _make_client(create_events=[])

    result = await retrieve_dept_filtered_context(client, "vs_123", "有趣的課")

    assert result is None
    search_mock.assert_not_called()


@pytest.mark.asyncio
async def test_retrieve_dept_filtered_context_returns_context_and_course_ids(monkeypatch):
    monkeypatch.setattr(dept_query, "extract_slots", _fake_extract_slots(department="歷史學系"))
    client, search_mock = _make_client(
        create_events=[],
        search_side_effect=[_AsyncIter([_search_result("071001001", "中國近代史課綱內容")])],
    )

    result = await retrieve_dept_filtered_context(client, "vs_123", "歷史系的課")

    assert result is not None
    context_text, course_ids = result
    assert course_ids == ["071001001"]
    assert "071001001" in context_text
    assert "中國近代史課綱內容" in context_text
