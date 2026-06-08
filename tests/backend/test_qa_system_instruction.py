# tests/backend/test_qa_system_instruction.py
"""_SYSTEM_INSTRUCTION 守護測試：grounding 命脈不可移除 + action-budget 約束已加入。"""

from backend.qa import _SYSTEM_INSTRUCTION


def test_system_instruction_has_grounding_rule_and_action_budget():
    assert "【鐵則・最高優先】" in _SYSTEM_INSTRUCTION      # grounding 命脈仍在
    assert "行動預算" in _SYSTEM_INSTRUCTION                # action-budget 約束已加
    assert "檢索效率" in _SYSTEM_INSTRUCTION               # 段落標題，措辭微調不誤紅


def test_system_instruction_has_anti_fabrication_table_rule():
    """表格反編造強化句必須存在，且不影響 grounding 命脈與 action-budget 段。"""
    assert "實際從 File Search 檢索到" in _SYSTEM_INSTRUCTION  # 強化反編造規則


def test_system_instruction_has_layer1_grounding_rules():
    """Layer 1：_SYSTEM_INSTRUCTION 必須含 context7 prompt-guidance 要求的 grounding 規則。

    - 「一字不差照抄」：模型提到課名時必須逐字複製檢索結果
    - 「嚴禁」+「發明」：明確禁止自行發明課名
    - 「誠實說沒有」或「誠實」+「課」相關句：沒有課時誠實表達
    - prose 與表格都適用（不只限制表格）
    """
    assert "一字不差照抄" in _SYSTEM_INSTRUCTION, (
        "Layer 1 grounding 規則缺少「一字不差照抄」——這是防止課名改寫的核心句"
    )
    assert "嚴禁" in _SYSTEM_INSTRUCTION and "發明" in _SYSTEM_INSTRUCTION, (
        "Layer 1 grounding 規則缺少「嚴禁…發明」系列措辭"
    )
    # 「沒有夠貼切的課」或「誠實說沒有」——防止為有答案而捏造
    has_no_match_guidance = (
        "沒有夠貼切" in _SYSTEM_INSTRUCTION
        or "誠實說沒有" in _SYSTEM_INSTRUCTION
        or ("誠實" in _SYSTEM_INSTRUCTION and "沒有" in _SYSTEM_INSTRUCTION)
    )
    assert has_no_match_guidance, (
        "Layer 1 grounding 規則缺少「查無對應時誠實說明」的指引"
    )
