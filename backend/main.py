# backend/main.py
from __future__ import annotations
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from google import genai

from backend.models import RecommendRequest, RecommendResponse
from backend.recommend import build_recommendation_instrumented, load_careers
from backend.logger import build_log_record, insert_log, update_judge_scores
from backend.judge import evaluate_recommendation
from backend.db import init_pool, close_pool, get_pool

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
