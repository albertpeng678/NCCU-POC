# tests/backend/test_qa_finalize.py
"""finalize_qa_answer：問答收尾共用 helper（citations join → 課名補 → 防幻覺覆寫）。"""
from backend.qa import finalize_qa_answer, NO_RESULTS_MESSAGE

_META = {
    "070415001": {"name": "資料科學基礎", "department": "社科院", "teacher": "杜福童",
                  "credits": 3, "syllabus_url": "http://x/1"},
}


def test_grounded_course_ids_become_citations():
    answer, followups, citations, no_match = finalize_qa_answer(
        "推薦 **資料科學基礎**。", ["q1"], ["070415001"], _META,
    )
    assert [c["course_id"] for c in citations] == ["070415001"]
    assert answer == "推薦 **資料科學基礎**。"
    assert followups == ["q1"]
    assert no_match is False


def test_empty_grounding_falls_back_to_name_match():
    # 無 course_ids，但答案提到知識庫真實課名 → 用課名補 citations
    answer, followups, citations, no_match = finalize_qa_answer(
        "我推薦資料科學基礎這門課。", ["q1"], [], _META,
    )
    assert [c["name"] for c in citations] == ["資料科學基礎"]
    assert answer == "我推薦資料科學基礎這門課。"  # 有 citation → 不覆寫
    assert no_match is False


def test_no_match_course_listing_triggers_override():
    # 空 citations + 答案像在列課程（含 9 碼代號）→ 防幻覺覆寫 + no_match 旗標
    answer, followups, citations, no_match = finalize_qa_answer(
        "課號 999999999 是一門好課。", ["q1"], [], _META,
    )
    assert citations == []
    assert answer == NO_RESULTS_MESSAGE
    assert followups == []
    assert no_match is True
