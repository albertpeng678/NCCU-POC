# tests/backend/test_qa_citations.py
"""extract_citations 前6碼去重：同一門課多班次（前6碼相同）只保留第一個。

根因：courses_meta.json 中同名課如「設計思維與軟體系統專案管理」有
  046008001 / 046008011 / 046008021（同名、同系所、同老師、前6碼相同、後3碼班次不同）
舊實作按完整 course_id 去重 → 3 個 id 不同 → 全保留 → 參考課綱顯示 3 個一樣的連結。
修法：改按前6碼去重，保序。
"""
from backend.qa import extract_citations

# 小型 meta dict 控制（避免載入 2718 筆 JSON 造成不必要的 I/O）
_META_SAME_PREFIX = {
    "046008001": {
        "name": "設計思維與軟體系統專案管理",
        "department": "資訊管理學系",
        "teacher": "張老師",
        "credits": 3,
        "syllabus_url": "https://example.com/046008001",
    },
    "046008011": {
        "name": "設計思維與軟體系統專案管理",
        "department": "資訊管理學系",
        "teacher": "張老師",
        "credits": 3,
        "syllabus_url": "https://example.com/046008011",
    },
    "046008021": {
        "name": "設計思維與軟體系統專案管理",
        "department": "資訊管理學系",
        "teacher": "張老師",
        "credits": 3,
        "syllabus_url": "https://example.com/046008021",
    },
    "070415001": {
        "name": "政治學概論",
        "department": "政治學系",
        "teacher": "李老師",
        "credits": 3,
        "syllabus_url": "https://example.com/070415001",
    },
    "752637001": {
        "name": "資料科學導論",
        "department": "統計學系",
        "teacher": "王老師",
        "credits": 3,
        "syllabus_url": "https://example.com/752637001",
    },
}


def test_same_prefix_deduped_to_one():
    """3 個前6碼相同的 course_id → 只保留第一個出現的。"""
    result = extract_citations(["046008001", "046008011", "046008021"], _META_SAME_PREFIX)
    assert len(result) == 1, f"應只剩1筆，實得 {len(result)}: {result}"
    assert result[0]["course_id"] == "046008001"


def test_different_prefix_kept_separately():
    """前6碼不同的兩個 course_id → 各自保留（不誤併）。"""
    result = extract_citations(["070415001", "752637001"], _META_SAME_PREFIX)
    assert len(result) == 2, f"應保留2筆，實得 {len(result)}: {result}"
    ids = [r["course_id"] for r in result]
    assert "070415001" in ids
    assert "752637001" in ids


def test_order_preserved_first_occurrence_wins():
    """先出現的前6碼先排；後出現的同前6碼被丟棄。"""
    # 故意把 021 排最前面
    result = extract_citations(["046008021", "046008001", "046008011"], _META_SAME_PREFIX)
    assert len(result) == 1
    assert result[0]["course_id"] == "046008021", "應保留最先出現的 046008021"


def test_mixed_same_and_different_prefix():
    """同前6碼 + 不同前6碼混排：同前6碼只留第一，不同前6碼各留一。"""
    result = extract_citations(
        ["046008001", "070415001", "046008011", "752637001", "046008021"],
        _META_SAME_PREFIX,
    )
    assert len(result) == 3, f"應3筆，實得 {len(result)}"
    ids = [r["course_id"] for r in result]
    assert ids == ["046008001", "070415001", "752637001"], f"順序不對: {ids}"


def test_missing_from_meta_still_skipped():
    """meta 裡沒有的 course_id 一律跳過（原有行為不變）。"""
    result = extract_citations(["999999999", "046008001"], _META_SAME_PREFIX)
    assert len(result) == 1
    assert result[0]["course_id"] == "046008001"


def test_enriched_fields_present():
    """回傳的 citation dict 應含 name / department / teacher / credits / syllabus_url。"""
    result = extract_citations(["070415001"], _META_SAME_PREFIX)
    assert result[0]["name"] == "政治學概論"
    assert result[0]["department"] == "政治學系"
    assert result[0]["teacher"] == "李老師"
    assert result[0]["credits"] == 3
    assert result[0]["syllabus_url"] == "https://example.com/070415001"
