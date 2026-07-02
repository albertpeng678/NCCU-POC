from scripts.backfill_dept_attributes import build_attributes


def test_build_attributes_preserves_existing_and_adds_facets():
    rec = {"course_id": "103123001", "department": "歷史一", "syllabus_url": "http://x"}
    mapping = {"歷史一": {"dept_canonical": "歷史學系", "college": "文學院"}}
    attrs = build_attributes("103123001", rec, mapping)
    assert attrs["course_id"] == "103123001"      # 既有保留
    assert attrs["syllabus_url"] == "http://x"
    assert attrs["dept_canonical"] == "歷史學系"    # 新增
    assert attrs["college"] == "文學院"
    assert attrs["degree_level"] == "學士"          # 規則推導
    assert len(attrs) <= 16


def test_build_attributes_unmapped_falls_to_other():
    rec = {"course_id": "999000001", "department": "不存在系", "syllabus_url": ""}
    attrs = build_attributes("999000001", rec, {})
    assert attrs["dept_canonical"] == "其他"
