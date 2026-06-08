# tests/backend/test_qa_stream_jsonfirst.py
"""stream_answer（OpenAI Responses API）：token 原樣透傳，無 JSON 包裝守門，無 fence 截斷。

Phase 2 注：Gemini stream_answer 做 JSON-first/prose-then-fence 兩模式守門（避免 JSON 鷹架洩出）。
OpenAI Responses API 模型直接吐 Markdown，不包 JSON，故無需守門：
- token 原樣透傳（含表格、粗體、各種 Markdown）
- answer_text = 累積全文（供 finalize_qa_answer 做後處理）
"""
import asyncio
from types import SimpleNamespace

from backend import qa


def _text_delta(delta: str):
    return SimpleNamespace(delta=delta)


def _output_item_done_file_search(results=None):
    item = SimpleNamespace(type="file_search_call", results=results or [])
    return SimpleNamespace(item=item)


def _other():
    return SimpleNamespace()


async def _aiter(items):
    for it in items:
        yield it


def _collect(events):
    async def _create(**kwargs):
        return _aiter(events)

    client = SimpleNamespace(responses=SimpleNamespace(create=_create))

    async def run():
        toks, done = [], None
        async for ev in qa.stream_answer(client, "vs_123", "問題", None):
            if ev["event"] == "token":
                toks.append(ev["data"]["text"])
            elif ev["event"] == "done":
                done = ev["data"]
        return toks, done

    return asyncio.run(run())


def test_plain_markdown_passthrough():
    """OpenAI 模型直接吐 markdown → 全文原樣串給前端，不截斷。"""
    events = [
        _text_delta("政大有"),
        _text_delta("幾門課"),
    ]
    toks, done = _collect(events)
    joined = "".join(toks)
    assert joined == "政大有幾門課"
    assert done["answer_text"] == "政大有幾門課"


def test_markdown_table_passthrough():
    """Markdown 表格直接透傳，不做 fence 截斷（非 Gemini JSON-wrapped）。"""
    events = [
        _text_delta("以下課程：\n\n"),
        _text_delta("| 課程 | 系所 |\n| --- | --- |\n"),
        _text_delta("| **資料探勘** | 資管系 |\n"),
    ]
    toks, done = _collect(events)
    joined = "".join(toks)
    assert "| 課程 | 系所 |" in joined
    assert "**資料探勘**" in joined
    assert "以下課程：" in joined
