# tests/backend/test_qa_system_instruction.py
"""_SYSTEM_INSTRUCTION 守護測試：grounding 命脈不可移除 + action-budget 約束已加入。"""

from backend.qa import _SYSTEM_INSTRUCTION


def test_system_instruction_has_grounding_rule_and_action_budget():
    assert "【鐵則・最高優先】" in _SYSTEM_INSTRUCTION      # grounding 命脈仍在
    assert "行動預算" in _SYSTEM_INSTRUCTION                # action-budget 約束已加
    assert "2 次 File Search" in _SYSTEM_INSTRUCTION
