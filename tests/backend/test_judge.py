from backend.judge import parse_judge_response, build_judge_prompt

def test_parse_judge_response_valid():
    raw = """{
  "relevance": 4,
  "grouping": 5,
  "reason_quality": 3,
  "diversity": 4,
  "critique": "推薦課程整體與PM職涯相關，但推薦理由可再具體。"
}"""
    scores = parse_judge_response(raw)
    assert scores["judge_relevance_score"] == 4
    assert scores["judge_grouping_score"] == 5
    assert scores["judge_reason_score"] == 3
    assert scores["judge_diversity_score"] == 4
    assert scores["judge_overall_score"] == 4  # round((4+5+3+4)/4)
    assert "PM職涯" in scores["judge_critique"]

def test_parse_judge_response_clamps_out_of_range():
    raw = '{"relevance": 6, "grouping": 0, "reason_quality": 3, "diversity": 3, "critique": "test"}'
    scores = parse_judge_response(raw)
    assert scores["judge_relevance_score"] == 5   # clamped to 5
    assert scores["judge_grouping_score"] == 1    # clamped to 1

def test_parse_judge_response_malformed_returns_none():
    assert parse_judge_response("not json") is None
    assert parse_judge_response("{}") is None

def test_build_judge_prompt_contains_career_and_courses():
    result = {
        "career": "產品經理(PM)",
        "groups": {
            "core": [{"name": "行銷管理", "department": "企管系", "reason": "培養市場敏感度"}],
            "supporting": [],
            "extended": [],
        }
    }
    prompt = build_judge_prompt(career="產品經理(PM)", result=result)
    assert "產品經理(PM)" in prompt
    assert "行銷管理" in prompt
    assert "relevance" in prompt
    assert "1-5" in prompt
