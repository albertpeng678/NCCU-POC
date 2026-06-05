# tests/backend/test_qa_partial_answer.py
"""_partial_answer：從半截串流 JSON 容錯解出當前 answer 欄位值。"""
from backend.qa import _partial_answer


def test_partial_growing_json():
    assert _partial_answer('{"answer": "政大在') == "政大在"
    assert _partial_answer('{"answer": "政大在 114-2') == "政大在 114-2"


def test_complete_json():
    assert _partial_answer('{"answer": "完整答案", "followup_suggestions": []}') == "完整答案"


def test_empty_or_garbage():
    assert _partial_answer("") == ""
    assert _partial_answer("{") == ""
