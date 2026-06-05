# tests/backend/test_qa_response_hardening.py
"""parse_qa_response 強化：json_repair 救壞 JSON；全失敗也絕不回傳含鷹架原文。"""
from backend.qa import parse_qa_response


def test_repairs_literal_newline_in_json_string():
    # 表格塞 JSON 字串常見：字面換行 → 標準 json.loads 失敗 → json_repair 救回
    raw = '```json\n{"answer": "第一行\n第二行", "followup_suggestions": ["q1"]}\n```'
    out = parse_qa_response(raw)
    assert "第一行" in out["answer"] and "第二行" in out["answer"]
    assert out["followup_suggestions"] == ["q1"]


def test_unrecoverable_never_leaks_scaffolding():
    raw = '```json\n{ 這完全不是合法 JSON ```'
    out = parse_qa_response(raw)
    assert "```" not in out["answer"]
    assert '{"answer"' not in out["answer"]
    assert "json" not in out["answer"].lower()
    assert out["followup_suggestions"] == []


def test_plain_prose_passthrough():
    out = parse_qa_response("這是一段沒有 JSON 的純文字回答。")
    assert out["answer"] == "這是一段沒有 JSON 的純文字回答。"


def test_dict_without_answer_key_blanked_not_leaked():
    # json 物件但無 answer 欄位 → 不得把 {"foo":"bar"} 當答案漏出，blank 較安全
    out = parse_qa_response('{"foo": "bar", "baz": 1}')
    assert out["answer"] == ""
    assert "foo" not in out["answer"]
    assert out["followup_suggestions"] == []
