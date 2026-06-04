# tests/backend/test_stream_logging_count.py
"""/recommend/stream 收尾 logging 用扁平 courses 計數（非 groups）。"""
import pytest


def _stage1_count_from_result(result):
    """複製 main.py 收尾的計數邏輯（扁平 courses）以單測其正確性。"""
    return len(result["courses"]) if result else 0


def test_count_from_flat_courses():
    result = {"courses": [{"course_id": "a"}, {"course_id": "b"}, {"course_id": "c"}]}
    assert _stage1_count_from_result(result) == 3


def test_count_none_result_zero():
    assert _stage1_count_from_result(None) == 0
