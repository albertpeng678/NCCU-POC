# tests/backend/test_qa_source_markers.py
"""strip_source_markers：剝除 file_search inline 來源引註標記（TDD）。"""
from backend.qa import strip_source_markers, finalize_qa_answer


# ─── 1. 純函式：strip_source_markers ───────────────────────────────────────

def test_strip_single_marker_after_sentence():
    result = strip_source_markers("報導 [tmpt_ync0ml.txt]。")
    assert result == "報導。"


def test_strip_multiple_files_in_one_bracket():
    result = strip_source_markers(
        "學生 [tmpt_ync0ml.txt, tmpvenglr3i.txt]。"
    )
    assert result == "學生。"


def test_no_markers_unchanged():
    text = "這是一段沒有標記的文字，**重點** 在此。"
    assert strip_source_markers(text) == text


def test_regular_brackets_not_removed():
    """一般方括號（不含 .txt）不可被移除。"""
    assert strip_source_markers("[註1]") == "[註1]"
    assert strip_source_markers("[參考文獻]") == "[參考文獻]"


def test_empty_string_returns_empty():
    assert strip_source_markers("") == ""


def test_none_returns_none():
    # 函式對 None 輸入應安全（返回原值）
    assert strip_source_markers(None) is None


def test_strip_inline_in_table_cell():
    row = "| **資料探勘** | 資訊管理 | 科技調查報導 [tmpt_ync0ml.txt]。 |"
    result = strip_source_markers(row)
    assert ".txt" not in result
    assert "資料探勘" in result


def test_strip_multiple_markers_in_text():
    text = "A [a.txt] 和 B [b.txt, c.txt] 都是好課。"
    result = strip_source_markers(text)
    assert result == "A 和 B 都是好課。"


# ─── 2. finalize_qa_answer 整合：含 [tmp.txt] 標記的 answer 回傳後必乾淨 ──

_META = {
    "070415001": {"name": "資料科學基礎", "department": "社科院", "teacher": "杜福童",
                  "credits": 3, "syllabus_url": "http://x/1"},
}


def test_finalize_strips_source_markers_from_answer():
    dirty_answer = "推薦 **資料科學基礎** [tmpt_ync0ml.txt, tmpvenglr3i.txt]。"
    answer, followups, citations, no_match = finalize_qa_answer(
        dirty_answer, ["q1"], ["070415001"], _META,
    )
    assert "tmp" not in answer
    assert ".txt" not in answer
    assert "資料科學基礎" in answer
    assert citations[0]["course_id"] == "070415001"
    assert no_match is False


def test_finalize_source_markers_stripped_before_no_match_check():
    """標記剝除後，防幻覺邏輯仍正確運作——乾淨文字讓 looks_like_course_listing 判斷更準。"""
    # answer 只有標記、無 9 碼代號、無表格 → 剝完後不像課程列表 → 不覆寫
    dirty_answer = "這門課很適合 [tmpt_ync0ml.txt]。"
    answer, followups, citations, no_match = finalize_qa_answer(
        dirty_answer, ["q1"], [], _META,
    )
    assert ".txt" not in answer
    assert no_match is False   # 無 course_listing，不應觸發覆寫
