# tests/backend/test_qa_judge.py
from backend.qa_judge import parse_qa_judge, build_qa_judge_prompt


def test_parse_qa_judge_valid():
    raw = '{"faithfulness":4,"relevancy":5,"context_precision":3,"critique":"答案忠於課綱"}'
    s = parse_qa_judge(raw)
    assert s["judge_faithfulness"] == 4
    assert s["judge_relevancy"] == 5
    assert s["judge_context_prec"] == 3
    assert s["judge_overall"] == 4  # round((4+5+3)/3)
    assert "課綱" in s["judge_critique"]


def test_parse_qa_judge_clamps():
    raw = '{"faithfulness":9,"relevancy":0,"context_precision":3,"critique":"x"}'
    s = parse_qa_judge(raw)
    assert s["judge_faithfulness"] == 5
    assert s["judge_relevancy"] == 1


def test_parse_qa_judge_malformed_none():
    assert parse_qa_judge("not json") is None


def test_build_qa_judge_prompt_contains_question_answer():
    p = build_qa_judge_prompt("哪些課教Python?", "課程A教Python", ["政治學"])
    assert "Python" in p
    assert "faithfulness" in p
