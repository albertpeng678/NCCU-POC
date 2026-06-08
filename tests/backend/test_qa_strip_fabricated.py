# tests/backend/test_qa_strip_fabricated.py
"""strip_fabricated_courses：確定性剝除表格中不在知識庫的假課列。"""
import pytest
from backend.qa import strip_fabricated_courses, finalize_qa_answer

# ── 小型知識庫 meta（給所有測試共用） ──
_META = {
    "111000001": {
        "name": "資料探勘",
        "department": "資訊管理學系",
        "teacher": "王老師",
        "credits": 3,
        "syllabus_url": "http://x/1",
    },
    "111000002": {
        "name": "統計學",
        "department": "統計學系",
        "teacher": "李老師",
        "credits": 3,
        "syllabus_url": "http://x/2",
    },
}


# ── helper：組一個標準表格字串 ──
def _make_table(*rows: list[str]) -> str:
    """rows 是 [課程名稱, 系所, 重點] 三欄字串。"""
    lines = [
        "| 課程名稱 | 系所 | 重點 |",
        "| --- | --- | --- |",
    ]
    for r in rows:
        lines.append(f"| {r[0]} | {r[1]} | {r[2]} |")
    return "\n".join(lines)


# ── Case 1：表格含 1 真課 + 2 假課 → 只留真課列，n_stripped==2 ──
def test_mixed_table_strips_fake_rows():
    table = _make_table(
        ["**資料探勘**", "資管系", "機器學習實作"],
        ["Creative Writing", "外文系", "英文寫作"],
        ["唐詩語言與句法分析", "中文系", "古詩分析"],
    )
    answer = f"以下是推薦課程：\n\n{table}\n\n請參考選修。"
    cleaned, n = strip_fabricated_courses(answer, _META)
    assert n == 2
    assert "Creative Writing" not in cleaned
    assert "唐詩語言與句法分析" not in cleaned
    assert "資料探勘" in cleaned
    # prose 說明仍在
    assert "以下是推薦課程" in cleaned
    assert "請參考選修" in cleaned


# ── Case 2：全真課 → 原樣不動，n_stripped==0 ──
def test_all_real_courses_unchanged():
    table = _make_table(
        ["**資料探勘**", "資管系", "機器學習"],
        ["統計學", "統計系", "推論統計"],
    )
    answer = f"推薦課程如下：\n\n{table}\n\n加油！"
    cleaned, n = strip_fabricated_courses(answer, _META)
    assert n == 0
    assert cleaned == answer


# ── Case 3：全假課 → 表格（含孤兒表頭/分隔線）整段移除、prose 說明保留 ──
def test_all_fake_courses_removes_entire_table():
    table = _make_table(
        ["Creative Writing", "外文系", "寫作"],
        ["唐詩語言與句法分析", "中文系", "古詩"],
    )
    prose_before = "這裡是一些說明文字。\n\n"
    prose_after = "\n\n希望對你有幫助。"
    answer = prose_before + table + prose_after
    cleaned, n = strip_fabricated_courses(answer, _META)
    assert n == 2
    assert "Creative Writing" not in cleaned
    assert "唐詩語言與句法分析" not in cleaned
    # 孤兒表頭和分隔線也應消失
    assert "課程名稱" not in cleaned
    assert "---" not in cleaned
    # prose 說明保留
    assert "這裡是一些說明文字" in cleaned
    assert "希望對你有幫助" in cleaned


# ── Case 4：無表格的純 prose → 完全不動 ──
def test_no_table_answer_unchanged():
    answer = "政大有很多很棒的課程，**資料探勘**是其中一門。可以考慮修習。"
    cleaned, n = strip_fabricated_courses(answer, _META)
    assert n == 0
    assert cleaned == answer


# ── Case 5：分隔線與表頭列不被當資料列誤刪 ──
def test_header_and_separator_not_treated_as_data_rows():
    # 只有表頭和分隔線，沒有資料列（極端情況）
    # 重點：n_stripped 必須是 0（表頭和分隔線不被當成「假課資料列」計入）
    answer = "| 課程名稱 | 系所 | 重點 |\n| --- | --- | --- |"
    cleaned, n = strip_fabricated_courses(answer, _META)
    # 分隔線和表頭不算假課資料列
    assert n == 0
    # 無資料列的空表格被整段移除是正確行為（孤兒表頭清理）；
    # 關鍵是沒有任何課名被誤算為 n_stripped。


# ── Case 6：課名含 **粗體** 的真課 → 去粗體後比對、正確保留 ──
def test_bold_real_course_preserved():
    table = _make_table(
        ["**資料探勘**", "資管系", "機器學習"],
    )
    answer = f"推薦：\n\n{table}"
    cleaned, n = strip_fabricated_courses(answer, _META)
    assert n == 0
    assert "資料探勘" in cleaned


# ── Case 7：finalize_qa_answer 整合 — 假課被剝除、citations 仍為真課 ──
def test_finalize_strips_fabricated_courses_from_answer():
    """表格含假課 + grounding 給真課 course_id → answer 不含假課、citations 仍為真課。"""
    table = _make_table(
        ["**統計學**", "統計系", "推論統計"],
        ["Creative Writing", "外文系", "英文寫作"],  # 假課
    )
    answer = f"推薦課程：\n\n{table}\n\n加油！"
    course_ids = ["111000002"]  # 只有統計學是真課（grounding 帶回）

    cleaned_answer, followups, citations, no_match = finalize_qa_answer(
        answer, ["下一個問題"], course_ids, _META,
    )
    assert "Creative Writing" not in cleaned_answer
    assert "統計學" in cleaned_answer
    assert len(citations) >= 1
    assert any(c["course_id"] == "111000002" for c in citations)
    assert no_match is False


# ── Case 8：非標準表頭（如「課名」）全假課 → 懸空分隔線不殘留 ──
def test_nonstandard_header_no_dangling_separator():
    """非標準表頭（課名）的全假課表格：表頭被當資料列剝掉後，分隔線不應懸空殘留。"""
    answer = "p\n\n| 課名 |\n| --- | --- |\n| 假課A | x |"
    cleaned, n = strip_fabricated_courses(answer, _META)
    # 假課 A 不在 meta → 被剝
    assert "假課A" not in cleaned
    # 不應殘留懸空分隔線（前一行不是表格行的 | --- |）
    lines = cleaned.split("\n")
    for idx, line in enumerate(lines):
        s = line.strip()
        if s.startswith("|") and set(s.replace("|", "").replace("-", "").replace(":", "").replace(" ", "")) == set():
            # 這是分隔線行，檢查前一行是否為表格行
            prev = lines[idx - 1].strip() if idx > 0 else ""
            assert prev.startswith("|"), f"Found dangling separator at line {idx}: prev={repr(prev)}"


# ── Case 9：fence 內的表格行不應被剝除 ──
def test_fence_inner_table_not_stripped():
    """``` 圍起來的程式碼區塊內含 | 表格格式 → 不被剝除。"""
    answer = (
        "以下是範例程式碼：\n\n"
        "```\n"
        "| 課名 | x |\n"
        "| --- | --- |\n"
        "| 假課B | y |\n"
        "```\n\n"
        "以上供參考。"
    )
    cleaned, n = strip_fabricated_courses(answer, _META)
    # fence 內的內容不應被剝除（n_stripped 應為 0）
    assert n == 0
    assert "假課B" in cleaned


# ── Case 10：兩個表格——第一個全假課被移除、第二個含真課保留 ──
def test_two_tables_first_fake_second_real():
    """答案含兩個表格：第一個全假課（應整段消失）、第二個含真課（應完整保留）。"""
    fake_table = _make_table(
        ["假課X", "外文系", "假課內容"],
        ["假課Y", "中文系", "假課內容"],
    )
    real_table = _make_table(
        ["**資料探勘**", "資管系", "機器學習實作"],
        ["統計學", "統計系", "推論統計"],
    )
    answer = f"前言。\n\n{fake_table}\n\n中間說明。\n\n{real_table}\n\n結語。"
    cleaned, n = strip_fabricated_courses(answer, _META)

    # 第一個表格的假課全消失
    assert n == 2
    assert "假課X" not in cleaned
    assert "假課Y" not in cleaned
    # 第一個孤兒表頭/分隔線也消失（不殘留）
    # 第二個表格的真課保留
    assert "資料探勘" in cleaned
    assert "統計學" in cleaned
    # prose 保留
    assert "前言" in cleaned
    assert "中間說明" in cleaned
    assert "結語" in cleaned
