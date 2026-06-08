# tests/backend/test_is_incomplete_answer.py
"""is_incomplete_answer 純函式：空、純空白 → True；正常文字 → False。
（3.5 偶發 TOO_MANY_TOOL_CALLS 空答案判定共用 helper）
"""

from backend.qa import is_incomplete_answer


def test_empty_string_is_incomplete():
    assert is_incomplete_answer("") is True


def test_whitespace_only_is_incomplete():
    assert is_incomplete_answer("   \n\t  ") is True


def test_normal_answer_is_not_incomplete():
    assert is_incomplete_answer("這是一個正常的答案") is False


def test_none_is_incomplete():
    """None 也視為不完整（防 parse_qa_response 回 None 的邊緣情況）。"""
    assert is_incomplete_answer(None) is True  # type: ignore[arg-type]
