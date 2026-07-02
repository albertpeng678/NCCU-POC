from backend.dept_vocab import (
    load_vocab, CANONICAL_DEPTS, DEPT_TO_COLLEGE,
    strip_grade_tokens, infer_degree_level,
)

def test_canonical_and_college_mapping():
    assert "歷史學系" in CANONICAL_DEPTS
    assert DEPT_TO_COLLEGE["歷史學系"] == "文學院"
    assert DEPT_TO_COLLEGE["資訊管理學系"] == "商學院"
    assert DEPT_TO_COLLEGE["資訊科學系"] == "資訊學院"

def test_strip_grade_tokens():
    assert strip_grade_tokens("歷史一") == "歷史"
    assert strip_grade_tokens("歷史碩一歷史博一歷史碩二歷史博二") == "歷史"
    assert strip_grade_tokens("中文三甲中文三乙") == "中文"
    assert strip_grade_tokens("歷史系") == "歷史系"  # 系字保留，靠 alias 對映

def test_infer_degree_level_by_rule():
    assert infer_degree_level("歷史一", "103xxxxxx") == "學士"
    assert infer_degree_level("歷史碩一歷史碩二", "153xxxxxx") == "碩士"
    assert infer_degree_level("歷史博一", "153xxxxxx") == "博士"
    assert infer_degree_level("歷史碩一歷史博一歷史碩二歷史博二", "153xxxxxx") == "碩博"

def test_infer_degree_level_ge_by_prefix():
    # 041/044 前綴的「歷史系」通識課
    assert infer_degree_level("歷史系", "041xxxxxx") == "通識"
