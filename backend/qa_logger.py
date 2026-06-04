# backend/qa_logger.py
from __future__ import annotations

import json
from typing import Optional


async def create_session(pool) -> Optional[str]:
    """Insert a new qa_session row. Returns session id as str, or None on failure."""
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "INSERT INTO qa_session DEFAULT VALUES RETURNING id::text"
            )
    except Exception as e:
        print(f"[qa_logger] create_session failed: {e}")
        return None


async def get_session(pool, session_id: str) -> Optional[dict]:
    """Fetch session row. Returns {last_interaction_id, turn_count} or None."""
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT last_interaction_id, turn_count FROM qa_session WHERE id=$1::uuid",
                session_id,
            )
            if row is None:
                return None
            return dict(row)
    except Exception as e:
        print(f"[qa_logger] get_session failed: {e}")
        return None


async def insert_turn(
    pool,
    session_id: str,
    turn_number: int,
    question: str,
    result: Optional[dict],
    error: Optional[Exception],
) -> Optional[int]:
    """Insert a qa_turn row. result is the answer_question dict or None.
    Returns the new row id, or None on failure.
    """
    if pool is None:
        return None
    try:
        success = error is None and result is not None
        answer = result.get("answer") if result else None
        citations = result.get("citations_course_ids", []) if result else []
        citation_count = len(citations)
        citations_json = json.dumps(citations) if citations else None
        followup = result.get("followup_suggestions", []) if result else []
        followup_json = json.dumps(followup) if followup else None
        latency_ms = result.get("latency_ms") if result else None
        error_type = type(error).__name__ if error else None
        error_message = str(error)[:500] if error else None

        async with pool.acquire() as conn:
            return await conn.fetchval(
                """INSERT INTO qa_turn (
                    session_id, turn_number, question,
                    answer, citation_count, citations_json, followup_json,
                    latency_ms, success, error_type, error_message
                ) VALUES (
                    $1::uuid, $2, $3,
                    $4, $5, $6::jsonb, $7::jsonb,
                    $8, $9, $10, $11
                ) RETURNING id""",
                session_id,
                turn_number,
                question,
                answer,
                citation_count,
                citations_json,
                followup_json,
                latency_ms,
                success,
                error_type,
                error_message,
            )
    except Exception as e:
        print(f"[qa_logger] insert_turn failed: {e}")
        return None


async def bump_session(pool, session_id: str, interaction_id: str) -> None:
    """Update session last_interaction_id and increment turn_count."""
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE qa_session
                   SET last_interaction_id=$1, turn_count=turn_count+1
                   WHERE id=$2::uuid""",
                interaction_id,
                session_id,
            )
    except Exception as e:
        print(f"[qa_logger] bump_session failed: {e}")


async def update_qa_judge(pool, turn_id: int, scores: dict) -> None:
    """Update judge score columns for an existing qa_turn row. Swallows errors."""
    if pool is None or not scores:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE qa_turn SET
                    judge_faithfulness=$1, judge_relevancy=$2,
                    judge_context_prec=$3, judge_overall=$4,
                    judge_critique=$5, judge_evaluated_at=NOW()
                WHERE id=$6""",
                scores["judge_faithfulness"],
                scores["judge_relevancy"],
                scores["judge_context_prec"],
                scores["judge_overall"],
                scores.get("judge_critique"),
                turn_id,
            )
    except Exception as e:
        print(f"[qa_logger] update_qa_judge failed for turn {turn_id}: {e}")


def build_history_from_turns(turns: list[dict]) -> list[dict]:
    """從 qa_turn 列轉出多輪問答歷史，**過濾掉失敗/半截輪**避免污染上下文。

    保留條件：success 為真（若無 success 欄則以 answer 非空為準）且 answer 去空白後非空。
    斷線寫下的半截 turn（answer=null、success=false）會被排除，不帶進 build_qa_contents。
    """
    history: list[dict] = []
    for t in turns:
        answer = t.get("answer")
        if not answer or not str(answer).strip():
            continue
        if "success" in t and not t.get("success"):
            continue
        history.append({"question": t.get("question"), "answer": answer})
    return history


async def get_session_turns(pool, session_id: str) -> list[dict]:
    """Fetch all turns for a session ordered by turn_number."""
    if pool is None:
        return []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM qa_turn
                   WHERE session_id=$1::uuid
                   ORDER BY turn_number ASC""",
                session_id,
            )
            return [dict(row) for row in rows]
    except Exception as e:
        print(f"[qa_logger] get_session_turns failed: {e}")
        return []
