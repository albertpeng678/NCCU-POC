# tests/backend/test_qa_stream_jsonfirst.py
"""stream_answer：模型 JSON-first（沒寫 prose 直接吐 JSON）時，串流不得漏 raw JSON 鷹架。"""
import asyncio
from backend import qa


class _Chunk:
    def __init__(self, text):
        self.text = text
        self.candidates = []


class _AStream:
    def __init__(self, texts):
        self._texts = texts

    def __aiter__(self):
        self._it = iter(self._texts)
        return self

    async def __anext__(self):
        try:
            return _Chunk(next(self._it))
        except StopIteration:
            raise StopAsyncIteration


class _AioModels:
    def __init__(self, texts):
        self._texts = texts

    async def generate_content_stream(self, **kwargs):
        return _AStream(self._texts)


class _Aio:
    def __init__(self, texts):
        self.models = _AioModels(texts)


class _Client:
    def __init__(self, texts):
        self.aio = _Aio(texts)


def _collect(texts):
    client = _Client(texts)

    async def run():
        toks, done = [], None
        async for ev in qa.stream_answer(client, "stores/x", "問題", None):
            if ev["event"] == "token":
                toks.append(ev["data"]["text"])
            elif ev["event"] == "done":
                done = ev["data"]
        return toks, done

    return asyncio.run(run())


def test_json_first_streams_clean_answer_only(monkeypatch):
    # 模型直接吐 JSON（沒 prose），分塊到達
    toks, done = _collect(['{"answer": "政大有', '幾門課', '", "followup_suggestions": []}'])
    joined = "".join(toks)
    assert '{"answer"' not in joined and "```" not in joined
    assert "政大有" in joined and "幾門課" in joined


def test_prose_then_fence_unchanged(monkeypatch):
    # 常態：prose 在前、```json 在後 → 只串 prose、遇 ``` 截斷
    toks, done = _collect(['政大有幾門課程。\n\n', '```json\n{"answer":"政大有幾門課程。","followup_suggestions":[]}\n```'])
    joined = "".join(toks)
    assert "政大有幾門課程。" in joined
    assert "```" not in joined and '{"answer"' not in joined
