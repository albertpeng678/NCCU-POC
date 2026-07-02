# tests/backend/test_qa_default_retrieval.py
"""預設受控檢索分支（qa-retr T4 Step B）：backend.qa.stream_answer 整合測試。

新增 backend.qa_retrieval.retrieve_and_rerank 作為「dept 分支落空後」的預設檢索路徑，
取代舊版「直接掛 file_search tool、模型自驅」當預設。三情境：

1. retrieve_and_rerank 有結果 → 走注入 context 生成（不掛 file_search tool），
   citations 用 retrieve_and_rerank 回傳的 ids。
2. retrieve_and_rerank 回 (None, [])／逾時／拋例外 → fail-open 落回既有 file_search tool 路徑，
   一字不改。
3. dept 分支（Task 7，extract_slots 偵測到系所）優先於這條新路徑——dept 分支命中時，
   retrieve_and_rerank 完全不會被呼叫。

tests/conftest.py 的 autouse fixture 已把 retrieve_and_rerank 預設鎖成 (None, [])
（避免其他既有測試意外觸發真實 OpenAI 呼叫）；本檔案的每個測試會自行 monkeypatch 覆寫。
"""
import asyncio

import pytest
from types import SimpleNamespace

import backend.qa as qa_mod
from backend import dept_query
from backend.qa import stream_answer, _SYSTEM_INSTRUCTION


# ── helpers（鏡射 test_qa_dept_filter.py / test_qa_stream_openai.py 既有慣例）──────

def _text_delta(delta: str):
    return SimpleNamespace(delta=delta)


def _output_item_done_file_search(results=None):
    item = SimpleNamespace(type="file_search_call", results=results or [])
    return SimpleNamespace(item=item)


async def _aiter(items):
    for it in items:
        yield it


class _AsyncStreamCM:
    def __init__(self, items):
        self._items = items

    def __aiter__(self):
        return _aiter(self._items).__aiter__()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _search_result(course_id: str):
    return SimpleNamespace(
        attributes={"course_id": course_id},
        filename=f"{course_id}.txt",
        content=[SimpleNamespace(text="課程內容")],
        score=0.9,
    )


def _make_client(create_events):
    async def _create(**kwargs):
        _create.calls.append(kwargs)
        return _AsyncStreamCM(create_events)
    _create.calls = []
    return SimpleNamespace(
        responses=SimpleNamespace(create=_create),
        vector_stores=SimpleNamespace(search=lambda **k: (_ for _ in ()).throw(
            AssertionError("vector_stores.search should not be called directly by stream_answer in this scenario")
        )),
    )


async def _collect(gen):
    return [ev async for ev in gen]


def _joined_tokens(events):
    return "".join(e["data"]["text"] for e in events if e["event"] == "token")


# ── 情境 1：retrieve_and_rerank 有結果 → 走注入 context 生成 ──────────────────

@pytest.mark.asyncio
async def test_default_retrieval_hit_uses_injected_context(monkeypatch):
    async def _fake_retrieve(question, vs_id):
        assert vs_id == "vs_123"
        return "【課名：資料探勘.txt｜系所：資訊管理學系】\n機器學習與資料採礦", ["091001001"]

    monkeypatch.setattr(qa_mod, "retrieve_and_rerank", _fake_retrieve)

    client = _make_client(create_events=[_text_delta("資料探勘很適合你")])
    result = await _collect(stream_answer(client, "vs_123", "我想學資料科學"))

    # 生成沒掛 file_search tool（context 已由門面注入）
    assert len(client.responses.create.calls) == 1
    gen_kwargs = client.responses.create.calls[0]
    assert "tools" not in gen_kwargs

    system_msg = next(m for m in gen_kwargs["input"] if m["role"] == "system")
    user_msg = next(m for m in gen_kwargs["input"] if m["role"] == "user")
    assert system_msg["content"].startswith(_SYSTEM_INSTRUCTION)
    assert "我想學資料科學" in user_msg["content"]
    assert "資料探勘" in user_msg["content"]

    assert _joined_tokens(result) == "資料探勘很適合你"
    done = [e for e in result if e["event"] == "done"][0]
    assert done["data"]["course_ids"] == ["091001001"]


# ── 情境 2：空/逾時/例外 → fail-open 落回既有 file_search 路徑 ─────────────────

@pytest.mark.asyncio
async def test_default_retrieval_empty_falls_back_to_file_search(monkeypatch):
    async def _fake_retrieve(question, vs_id):
        return None, []

    monkeypatch.setattr(qa_mod, "retrieve_and_rerank", _fake_retrieve)

    client = _make_client(create_events=[
        _output_item_done_file_search([_search_result("000211012")]),
        _text_delta("有趣的課有很多"),
    ])
    result = await _collect(stream_answer(client, "vs_123", "有什麼有趣的課"))

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


@pytest.mark.asyncio
async def test_default_retrieval_exception_falls_back_to_file_search(monkeypatch):
    async def _boom(question, vs_id):
        raise RuntimeError("模擬 API 掛掉")

    monkeypatch.setattr(qa_mod, "retrieve_and_rerank", _boom)

    client = _make_client(create_events=[_text_delta("正常答案")])
    result = await _collect(stream_answer(client, "vs_123", "有什麼有趣的課"))

    assert "tools" in client.responses.create.calls[0]
    assert _joined_tokens(result) == "正常答案"


@pytest.mark.asyncio
async def test_default_retrieval_timeout_falls_back_to_file_search(monkeypatch):
    async def _slow(question, vs_id):
        await asyncio.sleep(10)
        return "不該被用到", ["X"]

    monkeypatch.setattr(qa_mod, "retrieve_and_rerank", _slow)
    monkeypatch.setattr("backend.qa._QA_RETRIEVAL_TIMEOUT", 0.05)

    client = _make_client(create_events=[_text_delta("正常答案")])
    result = await _collect(stream_answer(client, "vs_123", "有什麼有趣的課"))

    assert "tools" in client.responses.create.calls[0]
    assert _joined_tokens(result) == "正常答案"


# ── 情境 3：dept 分支優先於這條新路徑 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_dept_branch_takes_priority_over_default_retrieval(monkeypatch):
    """extract_slots 偵測到系所 → dept 分支命中並提早 return，retrieve_and_rerank 完全不被呼叫。"""
    async def _fake_extract_slots(query):
        return {"department": "歷史學系", "college": None, "degree_level": None}

    monkeypatch.setattr(dept_query, "extract_slots", _fake_extract_slots)

    called = {"n": 0}

    async def _tracked_retrieve(question, vs_id):
        called["n"] += 1
        return "不該被用到", ["Y"]

    monkeypatch.setattr(qa_mod, "retrieve_and_rerank", _tracked_retrieve)

    class _AsyncIter:
        def __init__(self, items):
            self._items = items

        def __aiter__(self):
            async def gen():
                for it in self._items:
                    yield it
            return gen()

    def _search(**kwargs):
        return _AsyncIter([_search_result("071001001")])

    client = SimpleNamespace(
        responses=SimpleNamespace(create=None),
        vector_stores=SimpleNamespace(search=_search),
    )

    async def _create(**kwargs):
        _create.calls.append(kwargs)
        return _AsyncStreamCM([_text_delta("歷史系的課")])
    _create.calls = []
    client.responses.create = _create

    result = await _collect(stream_answer(client, "vs_123", "歷史系的課"))

    assert called["n"] == 0
    assert _joined_tokens(result) == "歷史系的課"
    done = [e for e in result if e["event"] == "done"][0]
    assert done["data"]["course_ids"] == ["071001001"]
