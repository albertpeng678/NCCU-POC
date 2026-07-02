from scripts.build_dept_mapping import distinct_dirty_values, parse_mapping_response


def test_distinct_dirty_values_dedups():
    meta = {"1": {"department": "歷史一"}, "2": {"department": "歷史一"}, "3": {"department": "中文系"}}
    assert sorted(distinct_dirty_values(meta)) == ["中文系", "歷史一"]


def test_parse_mapping_response_json():
    text = '```json\n{"歷史一":{"dept_canonical":"歷史學系","confidence":"high"}}\n```'
    out = parse_mapping_response(text)
    assert out["歷史一"]["dept_canonical"] == "歷史學系"
