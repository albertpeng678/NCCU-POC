# tests/backend/test_qa_thought_filter.py
"""串流不可外洩思維鏈：只取非 thought 的文字 part（官方 `if part.thought:` 過濾），
避免把模型 reasoning / 工具呼叫敘述（如 print(file_search.query(...))）串給使用者。
"""
from types import SimpleNamespace
from backend.qa import _visible_text_from_chunk


def _part(text, thought=None):
    return SimpleNamespace(text=text, thought=thought)


def _chunk(parts, fallback_text=None):
    cand = SimpleNamespace(content=SimpleNamespace(parts=parts))
    return SimpleNamespace(candidates=[cand], text=fallback_text)


def test_excludes_thought_parts():
    chunk = _chunk([
        _part("The user is asking... I will call file_search.\nprint(file_search.query(...))", thought=True),
        _part("哈囉！你問到李怡志教授…", thought=False),
    ])
    assert _visible_text_from_chunk(chunk) == "哈囉！你問到李怡志教授…"


def test_thought_true_only_returns_empty():
    chunk = _chunk([_part("internal reasoning", thought=True)])
    assert _visible_text_from_chunk(chunk) == ""


def test_multiple_visible_parts_concatenated():
    chunk = _chunk([_part("甲", thought=True), _part("乙"), _part("丙", thought=False)])
    assert _visible_text_from_chunk(chunk) == "乙丙"


def test_falls_back_to_chunk_text_when_no_parts():
    # 沒有 candidates/parts 結構 → 退回 chunk.text（相容舊形狀）
    chunk = SimpleNamespace(candidates=None, text="純文字 chunk")
    assert _visible_text_from_chunk(chunk) == "純文字 chunk"


def test_empty_chunk_returns_empty_string():
    chunk = SimpleNamespace(candidates=None, text=None)
    assert _visible_text_from_chunk(chunk) == ""
