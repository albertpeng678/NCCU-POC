# tests/backend/test_qa_citation_metadata.py
"""streaming citation 抽取：robust 讀 grounding_chunks 的結構化 custom_metadata.course_id
（官方 canonical 法），不再只靠脆弱的「掃文字找代號標頭」regex。
回歸鐵證：文件中段 chunk 沒有代號標頭、但 custom_metadata 帶 course_id → 必須抓到。
"""
from types import SimpleNamespace
from backend.qa import extract_course_ids_from_chunks


def _md(key, sval=None, nval=None):
    return SimpleNamespace(key=key, string_value=sval, numeric_value=nval)


def _chunk(grounding_chunks):
    cand = SimpleNamespace(grounding_metadata=SimpleNamespace(grounding_chunks=grounding_chunks))
    return SimpleNamespace(candidates=[cand], text="")


def _gc(text, custom_metadata):
    rc = SimpleNamespace(title="x.txt", uri=None, text=text, custom_metadata=custom_metadata)
    return SimpleNamespace(retrieved_context=rc)


def test_reads_course_id_from_custom_metadata_even_when_text_has_no_code():
    """chunk #4 情境：文字是文件中段、沒「課程代號:」標頭，但 custom_metadata 有 course_id。
    舊 regex 會漏 → 此測對舊碼為 RED。robust 版讀 custom_metadata → 抓到。"""
    chunk = _chunk([
        # 有標頭的 chunk（regex 與 metadata 都能抓）
        _gc("課程代號: 046008021\n課程名稱: 設計思維", [_md("course_id", "046008021")]),
        # 中段 chunk：文字無代號，但 metadata 有 → 不可漏
        _gc("14:45-16:00\n教室：商學院｜待通知\n本課程為三學分", [_md("course_id", "364855001")]),
    ])
    ids = extract_course_ids_from_chunks([chunk])
    assert ids == ["046008021", "364855001"], f"應抓到兩門（含中段 chunk），實得 {ids}"


def test_custom_metadata_numeric_value_also_supported():
    chunk = _chunk([_gc("無代號文字", [_md("course_id", None, 123456789)])])
    assert extract_course_ids_from_chunks([chunk]) == ["123456789"]


def test_regex_fallback_when_no_custom_metadata():
    """萬一某 chunk 沒 custom_metadata → 退回 regex 掃文字（保底相容）。"""
    chunk = _chunk([_gc("課程代號: 206814001\n專案管理", None)])
    assert extract_course_ids_from_chunks([chunk]) == ["206814001"]


def test_dedup_preserves_order():
    chunk = _chunk([
        _gc("a", [_md("course_id", "046008021")]),
        _gc("b", [_md("course_id", "046008021")]),  # 重複
        _gc("c", [_md("course_id", "364855001")]),
    ])
    assert extract_course_ids_from_chunks([chunk]) == ["046008021", "364855001"]


def test_ignores_non_course_id_metadata_keys():
    chunk = _chunk([_gc("無代號", [_md("syllabus_url", "https://x"), _md("course_id", "046008021")])])
    assert extract_course_ids_from_chunks([chunk]) == ["046008021"]
