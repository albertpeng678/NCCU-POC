import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from backend.retrieval_openai import search_skill, course_ids_from_annotations


# ── helpers ──────────────────────────────────────────────────────────────────

class _AsyncIter:
    """Wraps a plain list as an async iterable (mirrors AsyncPaginator behaviour)."""

    def __init__(self, items):
        self._items = items

    def __aiter__(self):
        async def gen():
            for it in self._items:
                yield it
        return gen()


def _fake_search_client(items):
    """Return a mock client whose vector_stores.search() is async-iterable."""
    c = MagicMock()
    c.vector_stores.search = MagicMock(return_value=_AsyncIter(items))
    return c


# ── search_skill tests ────────────────────────────────────────────────────────

def test_search_skill_normalizes_results():
    items = [
        SimpleNamespace(
            score=0.9,
            attributes={"course_id": "070415001"},
            content=[SimpleNamespace(text="資料科學課綱")],
        )
    ]
    out = asyncio.run(
        search_skill(_fake_search_client(items), "vs_1", "資料分析", top_k=8, score_threshold=0.0)
    )
    assert out == [{"course_id": "070415001", "score": 0.9, "content": "資料科學課綱"}]


def test_search_skill_skips_item_with_attributes_none():
    """item.attributes is None → skip silently, no crash."""
    items = [
        SimpleNamespace(score=0.5, attributes=None, content=[SimpleNamespace(text="X")]),
    ]
    out = asyncio.run(search_skill(_fake_search_client(items), "vs_1", "q"))
    assert out == []


def test_search_skill_empty_content():
    """item.content=[] → content field is empty string, not an error."""
    items = [
        SimpleNamespace(score=0.7, attributes={"course_id": "070415002"}, content=[]),
    ]
    out = asyncio.run(search_skill(_fake_search_client(items), "vs_1", "q"))
    assert out == [{"course_id": "070415002", "score": 0.7, "content": ""}]


def test_search_skill_skips_item_without_course_id():
    """attributes present but missing course_id key → skip."""
    items = [
        SimpleNamespace(score=0.6, attributes={}, content=[SimpleNamespace(text="Y")]),
    ]
    out = asyncio.run(search_skill(_fake_search_client(items), "vs_1", "q"))
    assert out == []


# ── course_ids_from_annotations tests ────────────────────────────────────────

def test_course_ids_from_annotations_strips_ext():
    anns = [
        SimpleNamespace(type="file_citation", filename="070415001.txt", file_id="f1"),
        SimpleNamespace(type="file_citation", filename="070415002.txt", file_id="f2"),
    ]
    assert course_ids_from_annotations(anns) == ["070415001", "070415002"]


def test_course_ids_dedup_and_ignore_nonfile():
    anns = [
        SimpleNamespace(type="file_citation", filename="070415001.txt", file_id="f1"),
        SimpleNamespace(type="url_citation", filename=None, file_id=None),
        SimpleNamespace(type="file_citation", filename="070415001.txt", file_id="f1"),
    ]
    assert course_ids_from_annotations(anns) == ["070415001"]


def test_course_ids_annotation_filename_none():
    """file_citation with filename=None → silently ignored."""
    anns = [
        SimpleNamespace(type="file_citation", filename=None, file_id="f1"),
        SimpleNamespace(type="file_citation", filename="070415003.txt", file_id="f2"),
    ]
    assert course_ids_from_annotations(anns) == ["070415003"]
