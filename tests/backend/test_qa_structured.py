# tests/backend/test_qa_structured.py
"""answer_question_structured：非串流 generate_content + response_schema，回乾淨結構化資料。"""
from backend.qa import answer_question_structured


class _Resp:
    def __init__(self, parsed, candidates=None, text=""):
        self.parsed = parsed
        self.text = text
        self.candidates = candidates or []


# --- grounding_metadata 物件形狀 mock（對齊真實非串流 generate_content response）---
class _MD:
    def __init__(self, key, sv):
        self.key = key
        self.string_value = sv
        self.numeric_value = None


class _RC:
    def __init__(self, cids):
        self.custom_metadata = [_MD("course_id", c) for c in cids]
        self.title = ""
        self.text = ""
        self.uri = ""


class _GC:
    def __init__(self, rc):
        self.retrieved_context = rc


class _GM:
    def __init__(self, gcs):
        self.grounding_chunks = gcs


class _Cand:
    def __init__(self, gm):
        self.grounding_metadata = gm
        self.content = None


def _resp_with_grounding(answer, cids, text=""):
    # 真實 grounding：一個 grounding_chunk / retrieved_context 帶一個 course_id
    gcs = [_GC(_RC([c])) for c in cids]
    return _Resp(
        parsed={"answer": answer, "followup_suggestions": []},
        candidates=[_Cand(_GM(gcs))],
        text=text,
    )


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
    # 結構化輸出 + thinking_level=low（3.5 GA 實測 grounding 不掉，加速用）
    assert cfg.response_mime_type == "application/json"
    assert cfg.response_schema is not None
    assert getattr(cfg, "thinking_config", None) is not None
    assert cfg.max_output_tokens == 8192


def test_system_instruction_keeps_retrieval_rule_drops_json_rule():
    from backend.qa import _SYSTEM_INSTRUCTION
    # grounding 命脈：先檢索鐵則必須留
    assert "先" in _SYSTEM_INSTRUCTION and "檢索" in _SYSTEM_INSTRUCTION
    # 不再教模型輸出 ```json 區塊（schema 接管結構）
    assert "```json" not in _SYSTEM_INSTRUCTION


def test_grounding_extracted_from_non_stream_candidates():
    # 真結構化 grounding：從 candidates[].grounding_metadata 取出 course_id
    resp = _resp_with_grounding("a", ["070415001", "356358001"])
    out = answer_question_structured(_FakeClient(resp), "stores/x", "q", history=None)
    assert out["citations_course_ids"] == ["070415001", "356358001"]


def test_no_phantom_citation_from_answer_envelope():
    # grounding 空 + answer/JSON 信封內含 9 碼 → 不得被當成 grounded citation
    # （結構化 response.text 是整包 JSON，掃信封文字會造出假 citation、繞過防幻覺覆寫）
    resp = _Resp(
        parsed={"answer": "代號 123456789 很讚", "followup_suggestions": []},
        candidates=[],
        text='{"answer":"代號 123456789 很讚","followup_suggestions":[]}',
    )
    out = answer_question_structured(_FakeClient(resp), "stores/x", "q", history=None)
    assert out["citations_course_ids"] == []
