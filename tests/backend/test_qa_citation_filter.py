# tests/backend/test_qa_citation_filter.py
"""
引用精準度：file_search 回傳 top_k 檢索全集，但模型不一定每門都引用。
finalize_qa_answer 應只把『答案實際提到課名』的檢索課列為 citations，
排除『檢索到但沒被引用』的無關課（如查 PM 卻冒出碳市場/排放權那門）。
邊界：若答案完全沒提到任何檢索課名 → 保留全集，不誤清空。
"""
from __future__ import annotations
from backend.qa import finalize_qa_answer

_META = {
    "100001001": {"name": "設計思維與軟體系統專案管理", "department": "資管系",
                  "teacher": "甲", "credits": 3.0, "syllabus_url": "u1"},
    "200002001": {"name": "碳市場與排放權交易之法律分析", "department": "法律系",
                  "teacher": "乙", "credits": 3.0, "syllabus_url": "u2"},
}


def test_unreferenced_retrieved_course_excluded_from_citations():
    # 答案只提第一門；第二門（碳市場）被檢索到但沒引用
    answer = ("如果你想往 PM 走，最對應的是 **設計思維與軟體系統專案管理**，"
              "它涵蓋設計思維、敏捷、MVP 與專案規劃。")
    a, f, citations, no_match = finalize_qa_answer(
        answer, [], ["100001001", "200002001"], _META,
        retrieved_ids={"100001001", "200002001"},
    )
    names = [c["name"] for c in citations]
    assert "設計思維與軟體系統專案管理" in names
    assert "碳市場與排放權交易之法律分析" not in names, "未被答案引用的檢索課不應出現在 citations"
    assert no_match is False


def test_keep_all_when_no_retrieved_name_referenced():
    # 邊界：答案沒提到任何檢索課名（純概念說明）→ 不誤清空，保留檢索全集
    answer = "專案管理大致分為範疇、時程、成本三大面向，建議先把基礎概念建立起來。"
    a, f, citations, no_match = finalize_qa_answer(
        answer, [], ["100001001", "200002001"], _META,
        retrieved_ids={"100001001", "200002001"},
    )
    assert len(citations) == 2, "全都沒被提到時應保留全集做 fallback，不清空"


def test_all_referenced_keeps_both():
    answer = ("兩門都推薦：**設計思維與軟體系統專案管理** 練專案能力，"
              "**碳市場與排放權交易之法律分析** 補產業法規視野。")
    a, f, citations, no_match = finalize_qa_answer(
        answer, [], ["100001001", "200002001"], _META,
        retrieved_ids={"100001001", "200002001"},
    )
    assert len(citations) == 2
