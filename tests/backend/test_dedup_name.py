# tests/backend/test_dedup_name.py
"""候選/結果依課名去重：避免跨掛同名課（同名不同 course_id）重複出現。"""
from backend.recommend import deduplicate_by_name


def test_dedup_by_name_keeps_first():
    items = [
        {"course_id": "000211012", "course_name": "資料科學基礎"},
        {"course_id": "000311022", "course_name": "資料科學基礎"},  # 同名不同系 → 去掉
        {"course_id": "000411033", "course_name": "機器學習"},
    ]
    out = deduplicate_by_name(items, "course_name")
    assert [c["course_id"] for c in out] == ["000211012", "000411033"]


def test_dedup_by_name_empty():
    assert deduplicate_by_name([], "course_name") == []


def test_dedup_by_name_uses_given_key():
    # 用 "name" 欄位（最終卡片用 name）
    items = [{"name": "統計學"}, {"name": "統計學"}, {"name": "微積分"}]
    out = deduplicate_by_name(items, "name")
    assert [c["name"] for c in out] == ["統計學", "微積分"]
