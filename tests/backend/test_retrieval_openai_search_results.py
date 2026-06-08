# tests/backend/test_retrieval_openai_search_results.py
"""course_ids_from_search_results：從 file_search_call.results 萃取 course_id。"""
from types import SimpleNamespace

from backend.retrieval_openai import course_ids_from_search_results


def _result(course_id=None, filename=None, score=0.9):
    attrs = {"course_id": course_id} if course_id else None
    return SimpleNamespace(attributes=attrs, filename=filename, score=score)


def test_extracts_from_attributes():
    results = [
        _result(course_id="703850001", filename="703850001.txt"),
        _result(course_id="090109001", filename="090109001.txt"),
    ]
    assert course_ids_from_search_results(results) == ["703850001", "090109001"]


def test_fallback_to_filename_when_no_attributes():
    results = [
        _result(course_id=None, filename="000211012.txt"),
        _result(course_id=None, filename="000212001.txt"),
    ]
    assert course_ids_from_search_results(results) == ["000211012", "000212001"]


def test_deduplicates_preserving_order():
    results = [
        _result(course_id="703850001"),
        _result(course_id="703850001"),  # duplicate
        _result(course_id="090109001"),
    ]
    assert course_ids_from_search_results(results) == ["703850001", "090109001"]


def test_empty_results():
    assert course_ids_from_search_results([]) == []


def test_none_results():
    assert course_ids_from_search_results(None) == []


def test_attributes_takes_priority_over_filename():
    """attributes.course_id 優先，不走 filename fallback。"""
    r = _result(course_id="703850001", filename="DIFFERENT.txt")
    assert course_ids_from_search_results([r]) == ["703850001"]
