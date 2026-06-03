# tests/backend/test_qa_format.py
"""Q&A 強化：格式驗證 / 重試判斷 / 空結果防幻覺覆寫 的單元測試。"""
from backend.qa import (
    looks_like_course_listing,
    needs_format_retry,
    should_override_no_results,
    NO_RESULTS_MESSAGE,
)


# --- looks_like_course_listing：判斷答案是否在「列具體課程」 ---

def test_listing_true_when_markdown_table_present():
    ans = (
        "以下是相關課程：\n\n"
        "| 課程名稱 | 系所 | 重點 |\n"
        "| --- | --- | --- |\n"
        "| **政治學** | **政治系** | **理論基礎** |\n"
    )
    assert looks_like_course_listing(ans) is True


def test_listing_true_when_nine_digit_course_code_present():
    assert looks_like_course_listing("推薦課程代號 000211012 政治學") is True


def test_listing_false_for_plain_concept_answer():
    ans = "**政治學**主要探討權力、制度與公共政策的形成，是一門基礎社會科學。"
    assert looks_like_course_listing(ans) is False


# --- should_override_no_results：citations 為空且看似列課程 → 須覆寫（防幻覺） ---

def test_override_true_when_listing_but_no_citations():
    ans = (
        "| 課程名稱 | 系所 | 重點 |\n| --- | --- | --- |\n"
        "| **量子金融** | **財管系** | **區塊鏈** |\n"
    )
    assert should_override_no_results(ans, []) is True


def test_override_false_when_citations_exist():
    ans = "| 課程名稱 | 系所 | 重點 |\n| --- | --- | --- |\n| **政治學** | **政治系** | x |\n"
    citations = [{"course_id": "000211012", "name": "政治學"}]
    assert should_override_no_results(ans, citations) is False


def test_override_false_for_concept_answer_without_citations():
    # 概念題沒列具體課程，即使無 citation 也不該覆寫（不是幻覺）
    ans = "**政治學**是研究權力與制度的學問。"
    assert should_override_no_results(ans, []) is False


# --- needs_format_retry：強制粗體（使用者明確要求重點用粗體） ---

def test_retry_true_when_no_bold():
    assert needs_format_retry("這是一段沒有任何粗體的課程說明文字。") is True


def test_retry_false_when_bold_present():
    assert needs_format_retry("這門課著重 **資料分析** 能力。") is False


def test_retry_false_for_empty_answer():
    # 空答案交給既有 fallback 處理，不觸發重試迴圈
    assert needs_format_retry("") is False


# --- NO_RESULTS_MESSAGE：溫和引導、不報錯 ---

def test_no_results_message_is_gentle_and_nonempty():
    assert isinstance(NO_RESULTS_MESSAGE, str)
    assert len(NO_RESULTS_MESSAGE) > 10
    # 不應是冷硬的錯誤語氣
    assert "錯誤" not in NO_RESULTS_MESSAGE
