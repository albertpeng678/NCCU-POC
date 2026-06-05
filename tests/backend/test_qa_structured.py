# tests/backend/test_qa_structured.py
"""answer_question_structured：非串流 generate_content + response_schema，回乾淨結構化資料。"""
from backend.qa import answer_question_structured


class _Resp:
    def __init__(self, parsed, candidates=None):
        self.parsed = parsed
        self.text = ""
        self.candidates = candidates or []


class _FakeModels:
    def __init__(self, resp):
        self._resp = resp
        self.last_kwargs = None

    def generate_content(self, **kwargs):
        self.last_kwargs = kwargs
        return self._resp


class _FakeClient:
    def __init__(self, resp):
        self.models = _FakeModels(resp)


def test_returns_clean_answer_and_followups():
    resp = _Resp(parsed={"answer": "資料科學課程介紹", "followup_suggestions": ["q1", "q2"]})
    client = _FakeClient(resp)
    out = answer_question_structured(client, "stores/x", "我想學資料科學", history=None, model="gemini-3.5-flash")
    assert out["answer"] == "資料科學課程介紹"
    assert out["followup_suggestions"] == ["q1", "q2"]
    assert "citations_course_ids" in out
    assert "latency_ms" in out


def test_passes_schema_and_model_to_generate_content():
    resp = _Resp(parsed={"answer": "a", "followup_suggestions": []})
    client = _FakeClient(resp)
    answer_question_structured(client, "stores/x", "q", history=None, model="gemini-3.5-flash")
    kw = client.models.last_kwargs
    assert kw["model"] == "gemini-3.5-flash"
    cfg = kw["config"]
    # 結構化輸出 + 無顯式 thinking_config（Probe B：三開會截斷/掉 grounding）
    assert cfg.response_mime_type == "application/json"
    assert cfg.response_schema is not None
    assert getattr(cfg, "thinking_config", None) is None
    assert cfg.max_output_tokens == 8192


def test_system_instruction_keeps_retrieval_rule_drops_json_rule():
    from backend.qa import _SYSTEM_INSTRUCTION
    # grounding 命脈：先檢索鐵則必須留
    assert "先" in _SYSTEM_INSTRUCTION and "檢索" in _SYSTEM_INSTRUCTION
    # 不再教模型輸出 ```json 區塊（schema 接管結構）
    assert "```json" not in _SYSTEM_INSTRUCTION
