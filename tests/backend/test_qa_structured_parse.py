# tests/backend/test_qa_structured_parse.py
"""parse_structured_response：三層容錯（parsed → json.loads → json_repair → 剝鷹架）。"""
from backend.qa import parse_structured_response


class _Resp:
    def __init__(self, parsed=None, text=""):
        self.parsed = parsed
        self.text = text


def test_uses_response_parsed_when_dict():
    r = _Resp(parsed={"answer": "嗨", "followup_suggestions": ["a"]})
    out = parse_structured_response(r)
    assert out["answer"] == "嗨"
    assert out["followup_suggestions"] == ["a"]


def test_falls_back_to_json_loads_when_parsed_none():
    r = _Resp(parsed=None, text='{"answer": "你好", "followup_suggestions": ["x", "y"]}')
    out = parse_structured_response(r)
    assert out["answer"] == "你好"
    assert out["followup_suggestions"] == ["x", "y"]


def test_json_repair_salvages_trailing_comma():
    r = _Resp(parsed=None, text='{"answer": "修", "followup_suggestions": ["x",],}')
    out = parse_structured_response(r)
    assert out["answer"] == "修"
    assert out["followup_suggestions"] == ["x"]


def test_strips_fence_when_all_else_fails():
    # 完全壞掉、無法解析 → 至少剝掉 ```json 鷹架，絕不把鷹架當答案
    r = _Resp(parsed=None, text='```json\n{ this is not json at all ```')
    out = parse_structured_response(r)
    assert "```" not in out["answer"]
    assert out["followup_suggestions"] == []
