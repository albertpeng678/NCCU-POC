# backend/main.py
from __future__ import annotations
import os
import json
import random
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from dotenv import load_dotenv
from google import genai
from google.genai import types
import sentry_sdk

from backend.models import (
    RecommendRequest, RecommendResponse,
    QaRequest, QaResponse,
)
from backend.recommend import (
    build_recommendation_instrumented_async, derive_skills_for_career_async,
    load_careers, load_courses_meta, stream_recommendation,
    stream_recommendation_from_budget, DEFAULT_BATCH_SIZE,
)
from backend.career_budget import get_budget
from backend.logger import build_log_record, insert_log, update_judge_scores
from backend.judge import evaluate_recommendation
from backend.db import init_pool, close_pool, get_pool
from backend.observability import before_send as sentry_before_send, stream_traces_sampler
from backend.qa import (
    answer_question_structured, finalize_qa_answer,
    extract_citations, stream_answer, stream_answer_structured,
    parse_qa_response, classify_qa_error, is_incomplete_answer,
    generate_followups, condense_question,
)
from backend.qa_judge import evaluate_qa
from backend.qa_logger import (
    create_session, get_session, insert_turn, bump_session,
    update_qa_judge, get_session_turns, build_history_from_turns,
)

from backend.openai_client import get_client as _get_openai_client
from backend.recommend import OPENAI_MODEL

load_dotenv()
_GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
_STORE_NAME = os.environ["FILE_SEARCH_STORE_NAME"]      # Gemini store（qa.py 仍用）
_VS_ID = os.environ.get("OPENAI_VECTOR_STORE_ID", "")   # OpenAI vector store（推薦用）
_ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
_QA_MODE = os.environ.get("QA_MODE", "stream")   # stream(預設 2.5 串流) | stream35(3.5 結構化串流) | replay(3.5 非串流)
# ⚠️（Session 9）預設改回 2.5（stream）：3.5 GA 上線窗口 high-demand 503 嚴重，時間敏感的問答經不起重試延遲
#    （見 CLAUDE #12 / HANDOFF Session 9）。3.5 容量回穩或 2.5 JSON 治本完成後再評估翻回 stream35。
# 啟動即驗證：打錯字(如 "Replay")不可靜默退回預設（會悄悄改行為、忽略 GEMINI_QA_MODEL）
if _QA_MODE not in {"replay", "stream", "stream35"}:
    raise RuntimeError(f"QA_MODE 必須是 'stream35' / 'stream' / 'replay'，收到 {_QA_MODE!r}")
_QA_MODEL = os.environ.get("GEMINI_QA_MODEL", "gemini-3.5-flash")  # 僅 replay 生效，須 3-series

# Sentry：錯誤監控 + tracing。SENTRY_DSN 未設則優雅停用（本機/無監控環境照常運作）。
# FastAPI/Starlette 整合由 sentry-sdk 自動偵測啟用。
_SENTRY_DSN = os.environ.get("SENTRY_DSN")
if _SENTRY_DSN:
    sentry_sdk.init(
        dsn=_SENTRY_DSN,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
        # 預期內暫時性 Gemini 錯誤(429/503/spending cap)降 warning+歸群、真 bug 維持 error；降級不丟棄
        before_send=sentry_before_send,
        # 超長串流端點(/recommend/stream ~2m、/qa/stream)取樣降至 0.1，避免污染 performance 報表
        traces_sampler=stream_traces_sampler,
        send_default_pii=False,
    )

# ⚠️ 429「high demand」風暴的真正根因：SDK 預設 timeout 僅 60s，但我們 recommend 檢索/分組
# 單次可達 ~80-100s（thinking + File Search）> 60s → client 逾時放棄、伺服器仍在跑 → retry 再送
# 新請求 → 同一邏輯呼叫分裂成多個併發請求 → 燒爆 RPM → 429。研究(Context7+web)：HttpOptions.timeout
# 單位毫秒、預設 60s，轉成 X-Server-Timeout header。把 timeout 設大(>最長請求)即可讓單一請求跑完、
# 不分裂、不誤觸發重試風暴。retry 仍保留供「真・速率/過載」429/503 退避(指數+jitter)。
_client = genai.Client(
    api_key=_GEMINI_API_KEY,
    http_options=types.HttpOptions(
        timeout=180_000,          # 180s（>最長請求 ~100s）→ 不再 60s 逾時、不再分裂成多請求
        retry_options=types.HttpRetryOptions(
            attempts=5,            # timeout 修好後不需太多次；保留供真 429/503 退避
            initial_delay=1.0,
            max_delay=20.0,
            exp_base=2.0,
            jitter=1.0,            # 隨機抖動，避免多請求同時重試撞牆
            http_status_codes=[429, 503],
        ),
    ),
)

# OpenAI client（推薦 fan-out 檢索 + stage2 標註）。
# 延遲建立（event loop 啟動後第一次 get_client() 呼叫）；OPENAI_API_KEY 未設時回 None（CI/test 環境）。
_openai_client = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _openai_client
    _openai_client = _get_openai_client()   # lazy init inside event loop（同一 loop 重用）
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
        scores = await evaluate_recommendation(_openai_client, career, result)
        if scores:
            await update_judge_scores(pool, log_id, scores)


@app.get("/health")
def health():
    # qa_mode 外露：線上實際跑哪個問答模式一目了然（curl /health 即知），
    # 用可觀測性取代「在 Railway 釘隱形環境變數」的技術債（見 CLAUDE #12 / HANDOFF Session 9）。
    # retrieval_backend/model 外露：Phase 1 OpenAI 遷移後可一眼確認推薦用 OpenAI。
    return {
        "status": "ok",
        "qa_mode": _QA_MODE,
        "retrieval_backend": "openai",
        "model": OPENAI_MODEL,
    }


@app.post("/recommend")
async def recommend(req: RecommendRequest, background_tasks: BackgroundTasks):
    careers = load_careers()
    seed = req.seed if req.seed is not None else random.randrange(1_000_000)

    # 清單外職涯：用 LLM 推導可轉移技能；推導不出 → no_match（不再 400）
    if req.career in careers:
        skills = None
        # 命中離線預算 → 直接回整池（秒出、0 次即時 fan-out/stage2）。未命中/無 DB → 落回即時。
        budget = await get_budget(get_pool(), req.career)
        if budget:
            return {
                "career": req.career,
                "courses": budget["courses"],
                "batch_size": DEFAULT_BATCH_SIZE,
                "latency_ms": 0,
                "seed": seed,
            }
    else:
        # async derive：直接 await 在主 loop（與 build pipeline 一致），不阻塞 event loop。
        # 改用 OpenAI client（Phase 1 遷移）。
        skills = await derive_skills_for_career_async(_openai_client, req.career)
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
        # async pipeline 直接 await 在主 loop（OpenAI AsyncOpenAI client，同一 loop 重用）。
        result, stage1_count = await build_recommendation_instrumented_async(
            _openai_client, _VS_ID, req.career, seed, skills=skills
        )
    except Exception as e:
        error = e
        sentry_sdk.capture_exception(e)   # 回報被吞掉的 Gemini/pipeline 錯誤（Sentry 未啟用時 no-op）

    # 清單外但檢索空 → no_match（誠實，不硬湊）
    if not error and skills is not None and result is not None and not result["courses"]:
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


# ===== SSE 串流端點（保留舊 POST 當 fallback）=====

def _sse(event: str, data: dict) -> dict:
    """把內部事件轉成 sse-starlette EventSourceResponse 接受的格式。"""
    return {"event": event, "data": json.dumps(data, ensure_ascii=False)}


# 背景任務強引用集合：asyncio fire-and-forget 任務需保活，避免被 GC 提前回收
_bg_tasks: set = set()


def _spawn_bg(coro) -> None:
    """fire-and-forget 一個 coroutine，保留強引用直到完成（不阻塞串流回應）。"""
    task = asyncio.ensure_future(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


@app.get("/recommend/stream")
async def recommend_stream(request: Request, career: str, seed: int | None = None):
    resolved_seed = seed if seed is not None else random.randrange(1_000_000)

    async def event_gen():
        result = None
        error = None
        # 命中離線預算 → 瞬間串流（0 即時 AI）；未命中/無 DB/清單外 → 即時串流
        budget = await get_budget(get_pool(), career) if career in load_careers() else None
        gen = (stream_recommendation_from_budget(career, budget, resolved_seed) if budget
               else stream_recommendation(_openai_client, _VS_ID, career, resolved_seed, skills=None))
        try:
            async for ev in gen:
                if await request.is_disconnected():
                    break  # 前端已關閉（收到終態 es.close()）→ 中止
                if ev["event"] == "result":
                    result = ev["data"]   # 收尾後補 logging/judge（與 POST /recommend 對齊）
                elif ev["event"] == "error":
                    error = Exception(ev["data"].get("message", "stream error"))
                yield _sse(ev["event"], ev["data"])
        except Exception as e:
            error = e
            sentry_sdk.capture_exception(e)
            yield _sse("error", {"error_type": type(e).__name__, "message": str(e)})

        # 串流路徑 logging/judge（即時路徑才需）。**命中預算（budget）跳過**：那是預先算好的池，
        # 重新 judge = 秒出卻背景燒一次 LLM（與 POST 命中一致、守「命中→0 即時 AI」不變式）。
        if not budget and (result is not None or error is not None):
            stage1_count = len(result["courses"]) if result else 0
            _spawn_bg(_background_log_and_judge(career, result, stage1_count, error))

    return EventSourceResponse(event_gen(), ping=15)


# ===== Q&A mode =====

async def _background_qa_judge(turn_id, question, answer, citation_names):
    """Phase 2: run Q&A judge, update scores. Non-blocking."""
    pool = get_pool()
    scores = await evaluate_qa(_openai_client, question, answer, citation_names)
    if scores:
        await update_qa_judge(pool, turn_id, scores)


@app.post("/qa", response_model=QaResponse)
async def qa(req: QaRequest, background_tasks: BackgroundTasks):
    pool = get_pool()

    # Resolve session: new or existing
    session_id = req.session_id
    turn_number = 1
    if session_id:
        sess = await get_session(pool, session_id)
        if sess is None:
            raise HTTPException(status_code=404, detail="Session not found")
        turn_number = (sess.get("turn_count") or 0) + 1
    else:
        # create_session 在無 DB 時回 ephemeral uuid（不再回 None）→ 移除硬性 503 死路徑。
        session_id = await create_session(pool)

    # Call Gemini（QA_MODE 切換新舊堆疊）。
    # 兩模式都用 history-based 多輪（stateless、穩定）；不碰 interactions API/`previous_interaction_id`
    # （beta 易碎，且串流回合把 last_interaction_id 存成 "" → 傳空字串給 interactions 必 400「Invalid previous_interaction_id」）。
    result = None
    error = None
    try:
        history = []
        if req.session_id:
            turns = await get_session_turns(pool, session_id)
            history = build_history_from_turns(turns)
        if _QA_MODE == "replay":
            result = await asyncio.to_thread(
                answer_question_structured, _client, _STORE_NAME, req.question, history, _QA_MODEL
            )
        else:
            # stream/stream35 模式 POST（前端 SSE 斷線時的 fallback）：drain 與 /qa/stream 同一條 history-based
            # 生成路徑成完整答案 → 兩路徑一致、零 interactions 依賴。
            _gen = stream_answer if _QA_MODE == "stream" else stream_answer_structured
            _gen_client = _openai_client if _QA_MODE == "stream" else _client
            _gen_store = _VS_ID if _QA_MODE == "stream" else _STORE_NAME
            # condense-then-search（與 GET /qa/stream 對齊）：stream(OpenAI) 模式且有歷史時，
            # 把簡短追問改寫成可獨立檢索的問題；持久化仍存原始 question（req.question）。
            if _QA_MODE == "stream" and history:
                q_for_search = await condense_question(_openai_client, req.question, history)
            else:
                q_for_search = req.question
            answer_text, course_ids = "", []
            async for ev in _gen(_gen_client, _gen_store, q_for_search, history):
                if ev["event"] == "done":
                    course_ids = ev["data"].get("course_ids", []) or []
                    answer_text = ev["data"].get("answer_text", "") or ""
            parsed = parse_qa_response(answer_text)
            result = {
                "answer": parsed["answer"],
                "followup_suggestions": parsed["followup_suggestions"],
                "citations_course_ids": course_ids,
                "latency_ms": 0,
            }
    except Exception as e:
        error = e
        sentry_sdk.capture_exception(e)   # 回報被吞掉的 Gemini 問答錯誤（Sentry 未啟用時 no-op）

    if error:
        # 失敗也記一筆 turn（result=None）以利後續排查，再回 503
        await insert_turn(pool, session_id, turn_number, req.question, None, error)
        raise HTTPException(status_code=503, detail=str(error))

    # 空答案守衛：3.5 偶發 TOO_MANY_TOOL_CALLS → 空答案；不落空白成功 turn，前端 fallback 對 503 走重試
    # 放在 try/except 外以避免被吞進 error 路徑（不寫 turn、不送 Sentry）
    if result is not None and is_incomplete_answer(result["answer"]):
        raise HTTPException(status_code=503, detail="incomplete answer")

    # 共用收尾：citations join + 課名補 + 防幻覺覆寫（與 /qa/stream 三處一致）。
    # ⚠️ 必須在 insert_turn 之前：否則幻覺答案（空 citations 卻像列課程）會以未覆寫原文存進 qa_turn，
    #    再經 build_history_from_turns 餵回下一輪污染上下文（/qa/stream 同樣持久化 finalize 後答案）。
    meta = load_courses_meta()
    # stream(OpenAI) 模式：course_ids 來自 file_search results → 模糊比對，容許模型改寫課名
    # replay/stream35(Gemini) 模式：retrieved_ids=None → 退回全 meta 完全比對（向後相容）
    _post_retrieved_ids = (
        set(result.get("citations_course_ids") or [])
        if _QA_MODE == "stream"
        else None
    )
    result["answer"], result["followup_suggestions"], citations, _no_match = finalize_qa_answer(
        result["answer"], result.get("followup_suggestions", []),
        result.get("citations_course_ids", []), meta,
        retrieved_ids=_post_retrieved_ids,
    )  # POST 是 fallback 路徑，不走分支③，no_match 略過

    # Persist turn (Phase 1) — 存 finalize 後答案
    turn_id = await insert_turn(pool, session_id, turn_number, req.question, result, error)
    # 兩模式皆 history-based、無 interaction_id → bump 空字串推進 turn_count（欄位已淘汰）
    await bump_session(pool, session_id, "")

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


@app.get("/qa/stream")
async def qa_stream(request: Request, question: str, session_id: str | None = None):
    pool = get_pool()

    # 解析 session（沿用 POST /qa 規則）
    turn_number = 1
    history: list = []
    if session_id:
        sess = await get_session(pool, session_id)
        if sess is None:
            async def _err():
                yield _sse("error", {"error_type": "NotFound", "message": "Session not found"})
            return EventSourceResponse(_err(), ping=15)
        turn_number = (sess.get("turn_count") or 0) + 1
        turns = await get_session_turns(pool, session_id)
        # 只帶成功輪進歷史：斷線半截 turn（answer=null/success=false）不污染上下文
        history = build_history_from_turns(turns)
    else:
        # create_session 無 DB → ephemeral uuid（不再回 None）→ 移除 DB-unavailable SSE error 死路徑。
        session_id = await create_session(pool)

    async def event_gen():
        meta = load_courses_meta()
        result_dict = None
        error = None
        try:
            if _QA_MODE == "replay":
                yield _sse("stage", {"label": "檢索課綱中…"})   # 立即觸發前端 firstEvent
                if await request.is_disconnected():
                    return
                result = await asyncio.to_thread(
                    answer_question_structured, _client, _STORE_NAME, question, history, _QA_MODEL
                )
                answer, followups, citations, no_match = finalize_qa_answer(
                    result["answer"], result["followup_suggestions"],
                    result["citations_course_ids"], meta,
                )
                result_dict = {
                    "answer": answer,
                    "citations_course_ids": result["citations_course_ids"],
                    "followup_suggestions": followups,
                    "latency_ms": result["latency_ms"],
                }
                yield _sse("done", {
                    "answer": answer,
                    "citations": citations,
                    "followup_suggestions": followups,
                    "no_match": no_match,   # 查無資料 → 前端走「換個問法」引導而非重試
                    "session_id": session_id,
                    "turn_number": turn_number,
                })
            else:
                _gen = stream_answer if _QA_MODE == "stream" else stream_answer_structured
                _gen_client = _openai_client if _QA_MODE == "stream" else _client
                _gen_store = _VS_ID if _QA_MODE == "stream" else _STORE_NAME
                # condense-then-search：簡短追問結合歷史改寫成可獨立檢索的問題（CondenseQuestion）。
                # 只在 stream(OpenAI) 模式且有歷史時改寫；stream35/replay 不動。
                # 持久化 turn 時仍存原始 question（使用者實際輸入，供歷史顯示）。
                if _QA_MODE == "stream" and history:
                    q_for_search = await condense_question(_openai_client, question, history)
                else:
                    q_for_search = question
                async for ev in _gen(_gen_client, _gen_store, q_for_search, history):
                    if await request.is_disconnected():
                        return  # 前端已關閉 → 中止
                    if ev["event"] == "token":
                        yield _sse("token", ev["data"])
                    elif ev["event"] == "done":
                        parsed = parse_qa_response(ev["data"]["answer_text"])
                        if is_incomplete_answer(parsed["answer"]):
                            # 3.5 偶發 TOO_MANY_TOOL_CALLS → 空答案；走既有 transient 重試泡泡，不落半截 turn
                            yield _sse("error", {"error_type": "incomplete", "message": "empty answer"})
                            return
                        # stream(OpenAI) 模式：course_ids 來自 file_search results → 模糊比對，容許模型改寫課名
                        # stream35(Gemini) 模式：retrieved_ids=None → 退回全 meta 完全比對（向後相容）
                        _stream_retrieved_ids = (
                            set(ev["data"]["course_ids"])
                            if _QA_MODE == "stream"
                            else None
                        )
                        answer, followups, citations, no_match = finalize_qa_answer(
                            parsed["answer"], parsed["followup_suggestions"],
                            ev["data"]["course_ids"], meta,
                            retrieved_ids=_stream_retrieved_ids,
                        )
                        # stream(OpenAI) 模式：OpenAI 吐純 markdown 無 JSON，followups 恆空；
                        # 兩段式補回：答案串完後快速呼叫 generate_followups（不帶 file_search）。
                        # stream35 模式答案已含 followup_suggestions（structured output），不重複呼叫。
                        if _QA_MODE == "stream" and not followups:
                            followups = await generate_followups(
                                _openai_client, question, answer
                            )
                        result_dict = {
                            "answer": answer,
                            "citations_course_ids": ev["data"]["course_ids"],
                            "followup_suggestions": followups,
                            "latency_ms": 0,
                        }
                        yield _sse("done", {
                            "answer": answer,   # 最終權威答案（含防幻覺覆寫）；前端據此覆蓋已串流文字
                            "citations": citations,
                            "followup_suggestions": followups,
                            "no_match": no_match,   # 查無資料 → 前端走「換個問法」引導而非重試
                            "session_id": session_id,
                            "turn_number": turn_number,
                        })
        except Exception as e:
            error = e
            sentry_sdk.capture_exception(e)
            # 語意化 error_type（rate_limited/timeout/unknown）→ 前端走差異化重試分支
            yield _sse("error", {"error_type": classify_qa_error(e), "message": str(e)})

        # 串流末持久化：**只在有完整 result_dict（走到 done）時才落 turn + bump**。
        # 斷線（mid-stream return，result_dict 仍為 None）或純錯誤 → 不寫半截 turn，
        # 避免 (1) answer=null/success=false 污染歷史 (2) turn_count 不前進導致下一輪 turn_number 重複。
        if result_dict:
            await insert_turn(pool, session_id, turn_number, question, result_dict, error)
            await bump_session(pool, session_id, "")

    return EventSourceResponse(event_gen(), ping=15)


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


# ===== 同源前端：backend 直接服務 frontend 靜態檔 =====
# 掛在最後 → 所有 API 路由（/health、/recommend、/qa…）優先；其餘路徑（/、/app.js…）給靜態檔。
# 部署時前後端同網址 → 零 CORS、單一 service、git push 觸發即更新。
# 目錄不存在（極端情境）時略過掛載，不崩啟動。
from pathlib import Path as _Path
from fastapi.staticfiles import StaticFiles

_FRONTEND_DIR = _Path(__file__).resolve().parent.parent / "frontend"
if _FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="static")
else:
    print(f"[static] frontend dir not found ({_FRONTEND_DIR}) — static serving disabled")
