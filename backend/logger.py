# backend/logger.py
from __future__ import annotations


def build_log_record(
    career: str,
    result: dict | None,
    stage1_count: int,
    error: Exception | None,
) -> dict:
    """Build a log record dict for inserting into query_log."""
    if error is not None:
        return {
            "career": career,
            "success": False,
            "latency_ms": result["latency_ms"] if result else None,
            "stage1_count": stage1_count,
            "result_core_count": None,
            "result_supporting_count": None,
            "result_extended_count": None,
            "error_type": type(error).__name__,
            "error_message": str(error),
        }

    groups = result.get("groups", {})
    return {
        "career": career,
        "success": True,
        "latency_ms": result.get("latency_ms"),
        "stage1_count": stage1_count,
        "result_core_count": len(groups.get("core", [])),
        "result_supporting_count": len(groups.get("supporting", [])),
        "result_extended_count": len(groups.get("extended", [])),
        "error_type": None,
        "error_message": None,
    }


async def insert_log(pool, record: dict) -> int | None:
    """Insert log record. Returns the new row id (for judge update), or None.
    Swallows exceptions so logging never breaks the API."""
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            return await conn.fetchval(
                """INSERT INTO query_log (
                    career, success, latency_ms, stage1_count,
                    result_core_count, result_supporting_count, result_extended_count,
                    error_type, error_message
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id""",
                record["career"], record["success"], record["latency_ms"],
                record["stage1_count"], record["result_core_count"],
                record["result_supporting_count"], record["result_extended_count"],
                record["error_type"], record["error_message"],
            )
    except Exception as e:
        print(f"[logger] insert failed: {e}")
        return None


async def update_judge_scores(pool, log_id: int, scores: dict) -> None:
    """Update judge score columns for an existing log row. Swallows errors."""
    if pool is None or not scores:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE query_log SET
                    judge_relevance_score=$1, judge_grouping_score=$2,
                    judge_reason_score=$3, judge_diversity_score=$4,
                    judge_overall_score=$5, judge_critique=$6,
                    judge_evaluated_at=NOW()
                WHERE id=$7""",
                scores["judge_relevance_score"], scores["judge_grouping_score"],
                scores["judge_reason_score"], scores["judge_diversity_score"],
                scores["judge_overall_score"], scores["judge_critique"], log_id,
            )
    except Exception as e:
        print(f"[logger] judge update failed for {log_id}: {e}")
