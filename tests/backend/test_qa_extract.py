# tests/backend/test_qa_extract.py
"""interactions response 文字/citation 萃取：需同時相容 google-genai 1.68.0(.outputs)
與 2.7.0(.steps / model_output / output_text) 的回應結構。"""
from types import SimpleNamespace
from backend.qa import extract_answer_text, extract_course_ids_from_grounding


# ---- 2.7.0 結構：steps -> model_output -> content[text] -> annotations[file_citation] ----

def _resp_270(text, course_id):
    ann = SimpleNamespace(
        type="file_citation",
        custom_metadata={"course_id": course_id, "syllabus_url": "https://x"},
        source=f"課程代號: {course_id}\r\n課程名稱: 政治學",
    )
    content = SimpleNamespace(type="text", text=text, annotations=[ann])
    return SimpleNamespace(
        steps=[SimpleNamespace(type="thought"),
               SimpleNamespace(type="model_output", content=[content])],
        output_text=text,
    )


def test_extract_text_270_steps():
    r = _resp_270("政治學在教 **基礎理論**。", "000211012")
    assert "基礎理論" in extract_answer_text(r)


def test_extract_course_ids_270_custom_metadata():
    r = _resp_270("政治學...", "000211012")
    assert extract_course_ids_from_grounding(r) == ["000211012"]


# ---- 1.68.0 結構：outputs -> item[text] -> annotations[file_citation].source ----

def _resp_168(text, course_id):
    ann = SimpleNamespace(type="file_citation", source=f"課程代號: {course_id}\r\n...")
    item = SimpleNamespace(type="text", text=text, annotations=[ann])
    return SimpleNamespace(outputs=[item])


def test_extract_text_168_outputs():
    r = _resp_168("政治學 **理論**", "000211012")
    assert "理論" in extract_answer_text(r)


def test_extract_course_ids_168_source_regex():
    r = _resp_168("政治學...", "000211012")
    assert extract_course_ids_from_grounding(r) == ["000211012"]


def test_extract_course_ids_empty_when_no_citations():
    r = SimpleNamespace(steps=[SimpleNamespace(type="model_output",
                          content=[SimpleNamespace(type="text", text="hi", annotations=[])])],
                        output_text="hi")
    assert extract_course_ids_from_grounding(r) == []
