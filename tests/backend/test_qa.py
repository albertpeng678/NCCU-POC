# tests/backend/test_qa.py
from backend.qa import extract_citations, parse_qa_response


def test_parse_qa_response_with_fenced_json():
    raw = '''Here is the answer.
```json
{"answer": "課程A教Python", "followup_suggestions": ["要先修嗎?", "幾學分?"]}
```'''
    r = parse_qa_response(raw)
    assert r["answer"] == "課程A教Python"
    assert len(r["followup_suggestions"]) == 2


def test_parse_qa_response_plain_text_fallback():
    # if no JSON, treat whole text as answer, empty followups
    r = parse_qa_response("這是純文字答案，沒有 JSON。")
    assert "純文字答案" in r["answer"]
    assert r["followup_suggestions"] == []


def test_extract_citations_maps_course_ids_to_meta():
    meta = {"000211012": {"name": "政治學", "department": "政治系", "teacher": "蔡", "credits": 3.0, "syllabus_url": "https://x/a"}}
    citations = extract_citations(["000211012", "NOTEXIST"], meta)
    assert len(citations) == 1
    assert citations[0]["name"] == "政治學"
    assert citations[0]["syllabus_url"] == "https://x/a"


def test_extract_citations_dedup():
    meta = {"000211012": {"name": "政治學", "department": "政治系", "teacher": "蔡", "credits": 3.0, "syllabus_url": "https://x/a"}}
    citations = extract_citations(["000211012", "000211012"], meta)
    assert len(citations) == 1
