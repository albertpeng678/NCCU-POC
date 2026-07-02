from scripts.build_dept_mapping import (
    build_mapping_prompt,
    clamp_to_canonical,
    distinct_dirty_values,
    parse_mapping_response,
)


def test_distinct_dirty_values_dedups():
    meta = {"1": {"department": "歷史一"}, "2": {"department": "歷史一"}, "3": {"department": "中文系"}}
    assert sorted(distinct_dirty_values(meta)) == ["中文系", "歷史一"]


def test_parse_mapping_response_json():
    text = '```json\n{"歷史一":{"dept_canonical":"歷史學系","confidence":"high"}}\n```'
    out = parse_mapping_response(text)
    assert out["歷史一"]["dept_canonical"] == "歷史學系"


def test_clamp_to_canonical_passes_through_valid_value():
    assert clamp_to_canonical("歷史學系", "high", {"歷史學系"}) == ("歷史學系", "high")


def test_clamp_to_canonical_clamps_invalid_value():
    assert clamp_to_canonical("不存在系", "high", {"歷史學系"}) == ("其他", "low")


def test_build_mapping_prompt_contains_guardrail_and_canonical_list():
    prompt = build_mapping_prompt(["歷史一"], ["歷史學系"])
    assert "只能用清單" in prompt
    assert "歷史學系" in prompt
