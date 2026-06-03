# backend/main.py
from __future__ import annotations
import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from google import genai

from backend.models import RecommendRequest, RecommendResponse
from backend.recommend import build_recommendation, load_careers

load_dotenv()
_GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
_STORE_NAME = os.environ["FILE_SEARCH_STORE_NAME"]
_ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")

app = FastAPI(title="NCCU Course Recommender")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[_ALLOWED_ORIGIN] if _ALLOWED_ORIGIN != "*" else ["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type"],
)

_client = genai.Client(api_key=_GEMINI_API_KEY)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/recommend", response_model=RecommendResponse)
def recommend(req: RecommendRequest):
    careers = load_careers()
    if req.career not in careers:
        raise HTTPException(status_code=400, detail=f"Unknown career: {req.career}")
    try:
        result = build_recommendation(_client, _STORE_NAME, req.career)
        return result
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
