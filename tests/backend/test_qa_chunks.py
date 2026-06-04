# tests/backend/test_qa_chunks.py
"""extract_course_ids_from_chunks：從 generate_content_stream 的累積 chunks 萃取 citations。
chunk.candidates[].grounding_metadata.grounding_chunks[].retrieved_context.{title,text}
含 9 碼 course_id；無結構化來源則掃描文字。"""
from types import SimpleNamespace
from backend.qa import extract_course_ids_from_chunks


def _chunk_with_grounding(course_id, text=""):
    rc = SimpleNamespace(title=f"課程代號: {course_id}", text="政治學課綱…",
                         uri=None)
    gchunk = SimpleNamespace(retrieved_context=rc)
    gm = SimpleNamespace(grounding_chunks=[gchunk])
    cand = SimpleNamespace(grounding_metadata=gm)
    return SimpleNamespace(text=text, candidates=[cand])


def _text_chunk(text):
    cand = SimpleNamespace(grounding_metadata=None)
    return SimpleNamespace(text=text, candidates=[cand])


def test_extract_from_grounding_chunks():
    chunks = [_chunk_with_grounding("000211012"), _text_chunk("…")]
    assert extract_course_ids_from_chunks(chunks) == ["000211012"]


def test_dedup_preserves_order():
    chunks = [_chunk_with_grounding("000211012"),
              _chunk_with_grounding("000216001"),
              _chunk_with_grounding("000211012")]
    assert extract_course_ids_from_chunks(chunks) == ["000211012", "000216001"]


def test_fallback_scans_text_for_9digit():
    chunks = [_text_chunk("推薦課程代號 000211012 很適合")]
    assert extract_course_ids_from_chunks(chunks) == ["000211012"]


def test_empty_when_nothing():
    assert extract_course_ids_from_chunks([_text_chunk("沒有任何碼")]) == []


def test_robust_to_missing_attrs():
    # chunk 結構殘缺不應炸
    assert extract_course_ids_from_chunks([SimpleNamespace()]) == []
