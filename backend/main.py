# backend/main.py
from __future__ import annotations
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from google import genai

from backend.models import (
    RecommendRequest, RecommendResponse,
    QaRequest, QaResponse,
)
from backend.recommend import build_recommendation_instrumented, load_careers, load_courses_meta
from backend.logger import build_log_record, insert_log, update_judge_scores
from backend.judge import evaluate_recommendation
from backend.db import init_pool, close_pool, get_pool
from backend.qa import answer_question, extract_citations
from backend.qa_judge import evaluate_qa
from backend.qa_logger import (
    create_session, get_session, insert_turn, bump_session,
    update_qa_judge, get_session_turns,
)

load_dotenv()
_GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
_STORE_NAME = os.environ["FILE_SEARCH_STORE_NAME"]
_ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")

_client = genai.Client(api_key=_GEMINI_API_KEY)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    yield
    await close_pool()


app = FastAPI(title="NCCU Course Recommender", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[_ALLOWED_ORIGIN] if _ALLOWED_ORIGIN != "*" else ["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type"],
)


async def _background_log_and_judge(career, result, stage1_count, error):
    """Phase 1: insert log row. Phase 2: run judge, update scores. Both non-blocking."""
    pool = get_pool()
    record = build_log_record(career, result, stage1_count, error)
    log_id = await insert_log(pool, record)
    if log_id and result:  # only judge successful recommendations
        scores = await evaluate_recommendation(_client, career, result)
        if scores:
            await update_judge_scores(pool, log_id, scores)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/recommend", response_model=RecommendResponse)
async def recommend(req: RecommendRequest, background_tasks: BackgroundTasks):
    careers = load_careers()
    if req.career not in careers:
        raise HTTPException(status_code=400, detail=f"Unknown career: {req.career}")

    result = None
    stage1_count = 0
    error = None
    try:
        result, stage1_count = build_recommendation_instrumented(_client, _STORE_NAME, req.career)
    except Exception as e:
        error = e

    # Fire-and-forget: log + judge (never blocks response)
    background_tasks.add_task(_background_log_and_judge, req.career, result, stage1_count, error)

    if error:
        raise HTTPException(status_code=503, detail=str(error))

    return result


# ===== Q&A mode =====

async def _background_qa_judge(turn_id, question, answer, citation_names):
    """Phase 2: run Q&A judge, update scores. Non-blocking."""
    pool = get_pool()
    scores = await evaluate_qa(_client, question, answer, citation_names)
    if scores:
        await update_qa_judge(pool, turn_id, scores)


@app.post("/qa", response_model=QaResponse)
async def qa(req: QaRequest, background_tasks: BackgroundTasks):
    pool = get_pool()

    # Resolve session: new or existing
    session_id = req.session_id
    prev_interaction_id = None
    turn_number = 1
    if session_id:
        sess = await get_session(pool, session_id)
        if sess is None:
            raise HTTPException(status_code=404, detail="Session not found")
        prev_interaction_id = sess.get("last_interaction_id")
        turn_number = (sess.get("turn_count") or 0) + 1
    else:
        session_id = await create_session(pool)
        if session_id is None:
            raise HTTPException(status_code=503, detail="Cannot create session (DB unavailable)")

    # Call Gemini
    result = None
    error = None
    try:
        result = answer_question(_client, _STORE_NAME, req.question, prev_interaction_id)
    except Exception as e:
        error = e

    # Persist turn (Phase 1)
    turn_id = await insert_turn(pool, session_id, turn_number, req.question, result, error)
    if result and result.get("interaction_id"):
        await bump_session(pool, session_id, result["interaction_id"])

    if error:
        raise HTTPException(status_code=503, detail=str(error))

    # Join citations with course metadata for full display info
    meta = load_courses_meta()
    citations = extract_citations(result.get("citations_course_ids", []), meta)

    # Fire-and-forget Q&A judge (Phase 2)
    if turn_id:
        citation_names = [c["name"] for c in citations]
        background_tasks.add_task(
            _background_qa_judge, turn_id, req.question, result["answer"], citation_names
        )

    return {
        "session_id": session_id,
        "turn_number": turn_number,
        "answer": result["answer"],
        "citations": citations,
        "followup_suggestions": result.get("followup_suggestions", []),
        "latency_ms": result.get("latency_ms", 0),
    }


@app.get("/qa/session/{session_id}")
async def qa_session(session_id: str):
    pool = get_pool()
    turns = await get_session_turns(pool, session_id)
    meta = load_courses_meta()
    # enrich each turn's citations
    for t in turns:
        cids = t.get("citations_json") or []
        t["citations"] = extract_citations(cids, meta)
    return {"session_id": session_id, "turns": turns}
