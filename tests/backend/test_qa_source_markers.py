# tests/backend/test_qa_source_markers.py
"""strip_source_markers：剝除 file_search inline 來源引註標記（TDD）。"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from backend.qa import strip_source_markers, finalize_qa_answer, stream_answer_structured


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


# ─── 3. stream_answer_structured：含來源標記的 token 串出後乾淨 ────────────

def _chunk_text(text, course_id=None):
    """Helper：建立無 thought 的 chunk，candidates 無 content（退回 chunk.text）。"""
    if course_id:
        rc = SimpleNamespace(
            custom_metadata=None,
            title=f"課程代號: {course_id}",
            text="", uri=None,
        )
        gm = SimpleNamespace(grounding_chunks=[SimpleNamespace(retrieved_context=rc)])
        cands = [SimpleNamespace(grounding_metadata=gm, content=None)]
    else:
        cands = [SimpleNamespace(grounding_metadata=None, content=None)]
    return SimpleNamespace(text=text, candidates=cands)


async def _aiter(items):
    for it in items:
        yield it


def _fake_client(chunks):
    aio = SimpleNamespace(models=SimpleNamespace(
        generate_content_stream=AsyncMock(return_value=_aiter(chunks))))
    return SimpleNamespace(aio=aio)


@pytest.mark.asyncio
async def test_stream_strips_source_markers_from_tokens():
    """串流 token 串接後不含 [tmp...txt] 標記，但正文完整。"""
    # 模擬 3.5 在 answer 中插入 inline 標記（標記跨兩個 chunk）
    chunks = [
        _chunk_text('{"answer": "推薦 **資料探勘** [tmpt_yn'),          # 標記在 chunk 邊界被切斷
        _chunk_text('c0ml.txt, tmpvenglr3i.txt]。課程很棒",'),
        _chunk_text(' "followup_suggestions": []}', course_id="000211012"),
    ]
    client = _fake_client(chunks)
    events = [ev async for ev in stream_answer_structured(client, "store", "問資料探勘", None)]

    tokens = "".join(e["data"]["text"] for e in events if e["event"] == "token")
    # 最終串接的 token 不應含 tmp*.txt 標記
    assert ".txt" not in tokens
    # 正文（課程名稱、其餘文字）應完整保留
    assert "資料探勘" in tokens
    assert "課程很棒" in tokens

    done = [e for e in events if e["event"] == "done"]
    assert len(done) == 1


@pytest.mark.asyncio
async def test_stream_clean_text_without_markers_unaffected():
    """無 inline 標記時，串流行為與現有行為一致，正文完整吐出。"""
    chunks = [
        _chunk_text('{"answer": "政治學'),
        _chunk_text(' 很棒",'),
        _chunk_text(' "followup_suggestions": []}', course_id="000211012"),
    ]
    client = _fake_client(chunks)
    events = [ev async for ev in stream_answer_structured(client, "store", "問政治學", None)]
    tokens = "".join(e["data"]["text"] for e in events if e["event"] == "token")
    assert tokens == "政治學 很棒"
