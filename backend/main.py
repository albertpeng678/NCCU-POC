# backend/main.py
from __future__ import annotations
import os
import random
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from google import genai
import sentry_sdk

from backend.models import (
    RecommendRequest, RecommendResponse,
    QaRequest, QaResponse,
)
from backend.recommend import (
    build_recommendation_instrumented, derive_skills_for_career,
    load_careers, load_courses_meta,
)
from backend.logger import build_log_record, insert_log, update_judge_scores
from backend.judge import evaluate_recommendation
from backend.db import init_pool, close_pool, get_pool
from backend.qa import (
    answer_question, extract_citations,
    should_override_no_results, NO_RESULTS_MESSAGE,
)
from backend.qa_judge import evaluate_qa
from backend.qa_logger import (
    create_session, get_session, insert_turn, bump_session,
    update_qa_judge, get_session_turns,
)

load_dotenv()
_GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
_STORE_NAME = os.environ["FILE_SEARCH_STORE_NAME"]
_ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")

# Sentry：錯誤監控 + tracing。SENTRY_DSN 未設則優雅停用（本機/無監控環境照常運作）。
# FastAPI/Starlette 整合由 sentry-sdk 自動偵測啟用。
_SENTRY_DSN = os.environ.get("SENTRY_DSN")
if _SENTRY_DSN:
    sentry_sdk.init(
        dsn=_SENTRY_DSN,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "1.0")),
        send_default_pii=False,
    )

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


@app.post("/recommend")
async def recommend(req: RecommendRequest, background_tasks: BackgroundTasks):
    careers = load_careers()
    seed = req.seed if req.seed is not None else random.randrange(1_000_000)

    # 清單外職涯：用 LLM 推導可轉移技能；推導不出 → no_match（不再 400）
    if req.career in careers:
        skills = None
    else:
        skills = derive_skills_for_career(_client, req.career)
        if not skills:
            return {
                "career": req.career,
                "no_match": True,
                "message": f"目前沒有找到對應「{req.career}」的課程，你可以用問答模式問我相關方向。",
            }

    result = None
    stage1_count = 0
    error = None
    try:
        result, stage1_count = build_recommendation_instrumented(
            _client, _STORE_NAME, req.career, seed, skills=skills
        )
    except Exception as e:
        error = e
        sentry_sdk.capture_exception(e)   # 回報被吞掉的 Gemini/pipeline 錯誤（Sentry 未啟用時 no-op）

    # 清單外但檢索空 → no_match（誠實，不硬湊）
    if not error and skills is not None and result is not None and not any(result["groups"].values()):
        return {
            "career": req.career,
            "no_match": True,
            "message": f"政大課程偏學術，目前沒有找到與「{req.career}」相關的課程，建議用問答模式探索。",
        }

    # Fire-and-forget: log + judge (never blocks response)
    background_tasks.add_task(_background_log_and_judge, req.career, result, stage1_count, error)

    if error:
        raise HTTPException(status_code=503, detail=str(error))

    # 清單外但有結果 → 補誠實 notice
    if skills is not None and result is not None:
        result.setdefault(
            "notice",
            f"政大沒有直接對應「{req.career}」的課程，但以下課程能培養相關的可轉移能力：",
        )
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
        sentry_sdk.capture_exception(e)   # 回報被吞掉的 Gemini 問答錯誤（Sentry 未啟用時 no-op）

    # Persist turn (Phase 1)
    turn_id = await insert_turn(pool, session_id, turn_number, req.question, result, error)
    if result and result.get("interaction_id"):
        await bump_session(pool, session_id, result["interaction_id"])

    if error:
        raise HTTPException(status_code=503, detail=str(error))

    # Join citations with course metadata for full display info
    meta = load_courses_meta()
    citations = extract_citations(result.get("citations_course_ids", []), meta)

    # 防幻覺：RAG 空命中卻列出具體課程 → 覆寫為誠實的查無資料引導，不放任模型自由文字
    if should_override_no_results(result["answer"], citations):
        result["answer"] = NO_RESULTS_MESSAGE
        result["followup_suggestions"] = []

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
