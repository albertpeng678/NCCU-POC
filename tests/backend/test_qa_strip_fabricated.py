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


# ═══════════════════════════════════════════════════════════════════════════
# retrieved_ids 模糊比對測試（TDD — 先寫 RED，修完應 GREEN）
# ═══════════════════════════════════════════════════════════════════════════

# 額外 meta：包含帶行政前綴的課名（政大體育常見格式）
_META_WITH_SPORT = {
    **_META,
    "002350001": {
        "name": "體育[男女合班]—武術初級",
        "department": "體育室",
        "teacher": "武術老師",
        "credits": 1,
        "syllabus_url": "http://x/3",
    },
    "002350002": {
        "name": "資料結構與演算法",
        "department": "資訊科學系",
        "teacher": "演算法老師",
        "credits": 3,
        "syllabus_url": "http://x/4",
    },
}


# ── Case 11（RED→GREEN）：retrieved_ids 模式，模型改寫課名仍保留 ──
def test_retrieved_ids_fuzzy_match_natural_name_kept():
    """retrieved_ids={002350001}，表格寫「武術初級」（模型自然縮寫）→ 不被砍（n==0）。

    「武術初級」 ⊂ 「體育[男女合班]—武術初級」→ 模糊命中 retrieved 課，應保留。
    """
    table = _make_table(
        ["武術初級", "體育室", "武術基礎入門"],
    )
    answer = f"推薦課程：\n\n{table}\n\n加油！"
    cleaned, n = strip_fabricated_courses(
        answer, _META_WITH_SPORT, retrieved_ids={"002350001"}
    )
    assert n == 0, f"武術初級 應被保留（retrieved_ids 模糊命中），但 n_stripped={n}"
    assert "武術初級" in cleaned


# ── Case 12（RED→GREEN）：retrieved_ids 模式，完全沒檢索到的假課應被砍 ──
def test_retrieved_ids_fully_fabricated_course_stripped():
    """retrieved_ids={002350001}，表格含「量子魔法導論」（完全沒檢索到也不在 meta）→ 砍掉。"""
    table = _make_table(
        ["武術初級", "體育室", "武術基礎入門"],
        ["量子魔法導論", "物理系", "神奇魔法"],
    )
    answer = f"推薦課程：\n\n{table}\n\n加油！"
    cleaned, n = strip_fabricated_courses(
        answer, _META_WITH_SPORT, retrieved_ids={"002350001"}
    )
    assert n == 1, f"量子魔法導論 應被砍，但 n_stripped={n}"
    assert "量子魔法導論" not in cleaned
    assert "武術初級" in cleaned


# ── Case 13（向後相容）：retrieved_ids=None → 退回全 meta 完全比對邏輯 ──
def test_retrieved_ids_none_falls_back_to_exact_match():
    """retrieved_ids=None（不傳）→ 退回原「對全 meta 完全比對」邏輯；既有測試行為不變。

    表格寫「武術初級」，retrieved_ids 未傳 → 完全比對全 meta 失敗（精確名含前綴）→ 被砍。
    （這正是 None 退回舊邏輯的預期行為。）
    """
    table = _make_table(
        ["武術初級", "體育室", "武術基礎入門"],
    )
    answer = f"推薦課程：\n\n{table}\n\n加油！"
    # 不傳 retrieved_ids（預設 None）→ 退回舊邏輯
    cleaned, n = strip_fabricated_courses(answer, _META_WITH_SPORT)
    # 舊邏輯：「武術初級」不在 meta 精確名集合（精確名是「體育[男女合班]—武術初級」）→ 被砍
    assert n == 1, "retrieved_ids=None 應退回舊邏輯：武術初級 不完全符合 meta 精確名 → 被砍"
    assert "武術初級" not in cleaned


# ── Case 14（retrieved_ids 模式，精確名也應命中）：retrieved_ids 帶精確課名 → 保留 ──
def test_retrieved_ids_exact_name_still_kept():
    """retrieved_ids={002350002}，表格寫精確課名「資料結構與演算法」→ 應保留（n==0）。"""
    table = _make_table(
        ["資料結構與演算法", "資訊科學系", "演算法基礎"],
    )
    answer = f"推薦課程：\n\n{table}"
    cleaned, n = strip_fabricated_courses(
        answer, _META_WITH_SPORT, retrieved_ids={"002350002"}
    )
    assert n == 0, "精確課名也應在 retrieved_ids 模式下命中（exact match 是 fuzzy 的子集）"
    assert "資料結構與演算法" in cleaned


# ── Case 15（retrieved_ids 為空集合）：空 set → 所有資料列都砍 ──
def test_retrieved_ids_empty_set_strips_all():
    """retrieved_ids=set()（空集合，無任何檢索結果）→ 所有資料列都被砍（n>0）。"""
    table = _make_table(
        ["資料探勘", "資管系", "機器學習"],
    )
    answer = f"推薦課程：\n\n{table}"
    cleaned, n = strip_fabricated_courses(
        answer, _META_WITH_SPORT, retrieved_ids=set()
    )
    assert n == 1, "retrieved_ids=set() 空集合應砍掉所有資料列"
    assert "資料探勘" not in cleaned


# ═══════════════════════════════════════════════════════════════════════════
# _norm_course_name + NFKC 正規化比對測試（TDD Layer 2 深化）
# ═══════════════════════════════════════════════════════════════════════════

# 這組測試驗證：retrieved_ids 模式下，_norm_course_name（NFKC + 行政詞/標點/空白去除）
# 讓括號、全形、空白、破折號等變體都能命中 retrieved 中的真課，不被誤砍。

def test_norm_course_name_helper_exists():
    """_norm_course_name 輔助函式必須存在於 backend.qa。"""
    import backend.qa as qa
    assert hasattr(qa, "_norm_course_name"), (
        "_norm_course_name helper 尚未定義——Layer 2 正規化比對需要此函式"
    )


def test_norm_course_name_strips_admin_prefix_and_punctuation():
    """_norm_course_name 應去掉行政前綴（體育、[男女合班]）與標點空白，回傳核心名稱。"""
    from backend.qa import _norm_course_name
    raw = "體育[男女合班]—武術初級"
    result = _norm_course_name(raw)
    # 行政詞與標點去除後，應只剩核心漢字
    assert "體育" not in result, f"期望去掉「體育」，但結果：{result!r}"
    assert "男女合班" not in result, f"期望去掉「男女合班」，但結果：{result!r}"
    assert "武術" in result, f"武術 應保留，但結果：{result!r}"
    assert "初級" in result, f"初級 應保留，但結果：{result!r}"


def test_norm_course_name_nfkc_fullwidth():
    """_norm_course_name 應對全形字元做 NFKC 正規化（全形→半形）。"""
    from backend.qa import _norm_course_name
    # 全形括號「（）」→ NFKC → 半形 ()，再被標點去除
    result_full = _norm_course_name("武術（初級）")
    result_half = _norm_course_name("武術初級")
    assert result_full == result_half, (
        f"NFKC 全形→半形後應與半形結果相等：{result_full!r} != {result_half!r}"
    )


# ── Case 16（RED→GREEN）：括號變體「武術（初級）」→ 不砍 ──
def test_retrieved_ids_parenthesis_variant_kept():
    """retrieved_ids={002350001}，表格寫「武術（初級）」（全形括號）→ 正規化互含後不被砍。

    NFKC 把全形（）轉半形 ()，再去標點 → 與 retrieved 正規化名互含 → 保留。
    """
    table = _make_table(
        ["武術（初級）", "體育室", "武術基礎入門"],
    )
    answer = f"推薦課程：\n\n{table}\n\n加油！"
    cleaned, n = strip_fabricated_courses(
        answer, _META_WITH_SPORT, retrieved_ids={"002350001"}
    )
    assert n == 0, f"武術（初級）應被保留（正規化互含），但 n_stripped={n}"
    assert "武術（初級）" in cleaned or "武術" in cleaned


# ── Case 17（RED→GREEN）：空白分隔「武術 初級」→ 不砍 ──
def test_retrieved_ids_space_variant_kept():
    """retrieved_ids={002350001}，表格寫「武術 初級」（含空白）→ 正規化去空白後互含 → 不被砍。"""
    table = _make_table(
        ["武術 初級", "體育室", "武術基礎入門"],
    )
    answer = f"推薦課程：\n\n{table}\n\n加油！"
    cleaned, n = strip_fabricated_courses(
        answer, _META_WITH_SPORT, retrieved_ids={"002350001"}
    )
    assert n == 0, f"武術 初級 應被保留（正規化去空白後互含），但 n_stripped={n}"
    assert "武術" in cleaned


# ── Case 18（RED→GREEN）：帶破折號前綴「體育—武術初級」→ 不砍 ──
def test_retrieved_ids_dash_prefix_variant_kept():
    """retrieved_ids={002350001}，表格寫「體育—武術初級」（無方括號）→ 正規化後互含 → 不被砍。"""
    table = _make_table(
        ["體育—武術初級", "體育室", "武術基礎入門"],
    )
    answer = f"推薦課程：\n\n{table}\n\n加油！"
    cleaned, n = strip_fabricated_courses(
        answer, _META_WITH_SPORT, retrieved_ids={"002350001"}
    )
    assert n == 0, f"體育—武術初級 應被保留（正規化後互含），但 n_stripped={n}"
    assert "武術" in cleaned


# ── Case 19（RED→GREEN）：完全不在 retrieved 的假課「AI新媒體影像創作與應用」→ 砍掉 ──
def test_retrieved_ids_ai_course_not_in_retrieved_stripped():
    """retrieved_ids={002350001}，表格寫「AI新媒體影像創作與應用」（meta 裡完全沒有）→ 被砍。

    這是已確診的幻覺案例：meta 裡完全無此課名，retrieved_ids 也無 → 正規化後也不命中 → 砍。
    """
    table = _make_table(
        ["武術初級", "體育室", "武術基礎入門"],
        ["AI新媒體影像創作與應用", "傳播學院", "AI影像製作"],
    )
    answer = f"推薦課程：\n\n{table}\n\n加油！"
    cleaned, n = strip_fabricated_courses(
        answer, _META_WITH_SPORT, retrieved_ids={"002350001"}
    )
    assert n == 1, f"AI新媒體影像創作與應用 應被砍（不在 retrieved 也不在 meta），但 n_stripped={n}"
    assert "AI新媒體影像創作與應用" not in cleaned
    assert "武術" in cleaned  # 真課仍保留
