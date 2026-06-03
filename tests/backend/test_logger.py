# tests/backend/test_logger.py
from backend.logger import build_log_record

def test_build_log_record_success():
    result = {
        "career": "產品經理(PM)",
        "groups": {
            "core": [{"course_id": "a"}] * 3,
            "supporting": [{"course_id": "b"}] * 3,
            "extended": [{"course_id": "c"}] * 2,
        },
        "latency_ms": 2500,
    }
    record = build_log_record(career="產品經理(PM)", result=result, stage1_count=15, error=None)
    assert record["career"] == "產品經理(PM)"
    assert record["success"] is True
    assert record["latency_ms"] == 2500
    assert record["stage1_count"] == 15
    assert record["result_core_count"] == 3
    assert record["result_supporting_count"] == 3
    assert record["result_extended_count"] == 2
    assert record["error_type"] is None
    assert record["error_message"] is None

def test_build_log_record_error():
    record = build_log_record(
        career="資料科學家",
        result=None,
        stage1_count=0,
        error=ValueError("stage1 returned 0 results"),
    )
    assert record["success"] is False
    assert record["error_type"] == "ValueError"
    assert "stage1 returned 0 results" in record["error_message"]
    assert record["result_core_count"] is None

def test_build_log_record_stage1_only_fails():
    record = build_log_record(
        career="行銷企劃",
        result=None,
        stage1_count=12,
        error=RuntimeError("stage2 schema mismatch"),
    )
    assert record["stage1_count"] == 12
    assert record["success"] is False
    assert record["error_type"] == "RuntimeError"
