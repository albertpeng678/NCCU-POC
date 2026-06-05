# tests/backend/test_qa_contents.py
"""build_qa_contents：把多輪歷史（最近 N 輪原文）組成 contents，末尾是本輪問題。"""
from backend.qa import build_qa_contents


def test_single_turn_no_history():
    c = build_qa_contents("我想學資料科學", history=None)
    assert isinstance(c, list)
    assert c[-1]["role"] == "user"
    assert "資料科學" in c[-1]["parts"][0]["text"]


def test_multi_turn_includes_history_in_order():
    history = [
        {"question": "Q1", "answer": "A1"},
        {"question": "Q2", "answer": "A2"},
    ]
    c = build_qa_contents("Q3", history=history)
    roles = [m["role"] for m in c]
    # user, model, user, model, user(本輪)
    assert roles == ["user", "model", "user", "model", "user"]
    assert c[0]["parts"][0]["text"] == "Q1"
    assert c[1]["parts"][0]["text"] == "A1"
    assert "Q3" in c[-1]["parts"][0]["text"]


def test_history_truncated_to_last_3_turns():
    history = [{"question": f"Q{i}", "answer": f"A{i}"} for i in range(6)]
    c = build_qa_contents("Qnow", history=history)
    # 最近 3 輪 = 6 則 history + 1 則本輪 = 7
    assert len(c) == 7
    assert c[0]["parts"][0]["text"] == "Q3"  # Q0..Q2 被截掉


def test_empty_history_list_same_as_none():
    assert build_qa_contents("hi", history=[]) == build_qa_contents("hi", history=None)


def test_structured_template_has_no_json_instruction():
    from backend.qa import build_qa_contents, _PROMPT_TEMPLATE_STRUCTURED
    c = build_qa_contents("我想學資料科學", history=None, template=_PROMPT_TEMPLATE_STRUCTURED)
    last = c[-1]["parts"][0]["text"]
    assert "我想學資料科學" in last
    # 結構化模板不得再教模型輸出 JSON 區塊（schema 接管）
    assert "json" not in last.lower()
    assert "followup_suggestions" not in last
