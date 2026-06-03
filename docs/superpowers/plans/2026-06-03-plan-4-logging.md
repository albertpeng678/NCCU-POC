# Postgres Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`.
>
> **Prerequisite:** Plan 2 (Backend) complete. Railway Postgres service added to project.

**Goal:** Add two-phase logging to the backend. Phase 1: immediately log request metadata (career, latency, result counts, errors). Phase 2: async LLM judge evaluates recommendation quality (4 dimensions, 1-5 each) and updates the log row — all non-blocking.

**Architecture:** `asyncpg` pool at startup. `backend/logger.py` handles insert + update. `backend/judge.py` runs Gemini judge evaluation as background task after response sent.

**Tech Stack:** asyncpg>=0.29, Railway Postgres (DATABASE_URL env var), google-genai (reuse existing key)

---

## File Map

| File | Responsibility |
|------|---------------|
| `backend/db.py` | asyncpg pool init/teardown, `get_pool()` |
| `backend/logger.py` | `insert_log()` (Phase 1) + `update_judge_scores()` (Phase 2) |
| `backend/judge.py` | Gemini LLM judge — evaluates 4 quality dimensions, returns scores |
| `tests/backend/test_logger.py` | Unit tests for log record building |
| `tests/backend/test_judge.py` | Unit tests for judge prompt + score parsing |
| `backend/main.py` | Modified: BackgroundTasks for both logging and judging |

---

## Task 1: Database Setup + Schema

- [ ] **Step 1: Add Railway Postgres to project**

In Railway dashboard:
1. Open NCCU-poc project
2. Click **+ New** → **Database** → **PostgreSQL**
3. Go to **Variables** tab of backend service
4. Railway auto-injects `DATABASE_URL` from the linked Postgres

Verify: `DATABASE_URL` appears in backend service variables.

- [ ] **Step 2: Add asyncpg to requirements**

Append to `backend/requirements.txt`:
```
asyncpg>=0.29
```

```bash
pip install asyncpg
```

- [ ] **Step 3: Create schema via Railway Postgres console**

In Railway → Postgres service → **Query** tab, run:

```sql
CREATE TABLE IF NOT EXISTS query_log (
    id                      SERIAL PRIMARY KEY,
    created_at              TIMESTAMPTZ DEFAULT NOW(),

    -- Phase 1: Request metadata (written immediately)
    career                  VARCHAR(100) NOT NULL,
    success                 BOOLEAN NOT NULL,
    latency_ms              INTEGER,
    stage1_count            INTEGER,
    result_core_count       INTEGER,
    result_supporting_count INTEGER,
    result_extended_count   INTEGER,
    error_type              VARCHAR(50),
    error_message           TEXT,

    -- Phase 2: LLM judge scores (written async, ~5-10s later)
    judge_relevance_score   SMALLINT,      -- 1-5: courses match career goal
    judge_grouping_score    SMALLINT,      -- 1-5: core/supporting/extended categorization
    judge_reason_score      SMALLINT,      -- 1-5: reasons are specific and actionable
    judge_diversity_score   SMALLINT,      -- 1-5: variety across departments/fields
    judge_overall_score     SMALLINT,      -- 1-5: weighted average
    judge_critique          TEXT,          -- LLM 1-2 sentence overall assessment
    judge_evaluated_at      TIMESTAMPTZ    -- NULL until judge completes
);

CREATE INDEX idx_query_log_career ON query_log(career);
CREATE INDEX idx_query_log_created_at ON query_log(created_at DESC);
CREATE INDEX idx_query_log_success ON query_log(success);
CREATE INDEX idx_query_log_judge_overall ON query_log(judge_overall_score);
```

- [ ] **Step 4: Verify table exists**

```sql
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name = 'query_log' ORDER BY ordinal_position;
```

Expected: 11 columns listed.

- [ ] **Step 5: Commit requirements update**

```bash
git add backend/requirements.txt
git commit -m "chore(backend): add asyncpg for Postgres logging"
```

---

## Task 2: `db.py` + `logger.py` (TDD)

**Files:**
- Create: `backend/db.py`
- Create: `backend/logger.py`
- Create: `tests/backend/test_logger.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/backend/test_logger.py
import pytest
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
    """If stage2 crashes after stage1 succeeds, stage1_count is still recorded."""
    record = build_log_record(
        career="行銷企劃",
        result=None,
        stage1_count=12,
        error=RuntimeError("stage2 schema mismatch"),
    )
    assert record["stage1_count"] == 12
    assert record["success"] is False
    assert record["error_type"] == "RuntimeError"
```

- [ ] **Step 2: Run tests to verify fail**

```bash
pytest tests/backend/test_logger.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement `backend/logger.py`**

```python
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


async def log_query(pool, record: dict) -> None:
    """Insert a log record. Swallows exceptions so logging never breaks the API."""
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO query_log (
                    career, success, latency_ms, stage1_count,
                    result_core_count, result_supporting_count, result_extended_count,
                    error_type, error_message
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                """,
                record["career"],
                record["success"],
                record["latency_ms"],
                record["stage1_count"],
                record["result_core_count"],
                record["result_supporting_count"],
                record["result_extended_count"],
                record["error_type"],
                record["error_message"],
            )
    except Exception as e:
        # Logging failure must never crash the API
        print(f"[logger] Failed to write log: {e}")
```

- [ ] **Step 4: Implement `backend/db.py`**

```python
# backend/db.py
from __future__ import annotations
import os
import asyncpg


_pool = None


async def init_pool() -> None:
    global _pool
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("[db] DATABASE_URL not set — logging disabled")
        return
    _pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
    print("[db] Postgres pool initialized")


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def get_pool():
    return _pool
```

- [ ] **Step 5: Run tests — verify pass**

```bash
pytest tests/backend/test_logger.py -v
```

Expected: 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/logger.py tests/backend/test_logger.py
git commit -m "feat(backend): Postgres query_log table with build_log_record helper"
```

---

## Task 3: LLM Judge — `judge.py` (TDD)

**Files:**
- Create: `backend/judge.py`
- Create: `tests/backend/test_judge.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/backend/test_judge.py
from backend.judge import parse_judge_response, build_judge_prompt

def test_parse_judge_response_valid():
    raw = """{
  "relevance": 4,
  "grouping": 5,
  "reason_quality": 3,
  "diversity": 4,
  "critique": "推薦課程整體與PM職涯相關，但推薦理由可再具體。"
}"""
    scores = parse_judge_response(raw)
    assert scores["judge_relevance_score"] == 4
    assert scores["judge_grouping_score"] == 5
    assert scores["judge_reason_score"] == 3
    assert scores["judge_diversity_score"] == 4
    assert scores["judge_overall_score"] == 4  # round((4+5+3+4)/4)
    assert "PM職涯" in scores["judge_critique"]

def test_parse_judge_response_clamps_out_of_range():
    raw = '{"relevance": 6, "grouping": 0, "reason_quality": 3, "diversity": 3, "critique": "test"}'
    scores = parse_judge_response(raw)
    assert scores["judge_relevance_score"] == 5   # clamped to 5
    assert scores["judge_grouping_score"] == 1    # clamped to 1

def test_parse_judge_response_malformed_returns_none():
    assert parse_judge_response("not json") is None
    assert parse_judge_response("{}") is None

def test_build_judge_prompt_contains_career_and_courses():
    result = {
        "career": "產品經理(PM)",
        "groups": {
            "core": [{"name": "行銷管理", "department": "企管系", "reason": "培養市場敏感度"}],
            "supporting": [],
            "extended": [],
        }
    }
    prompt = build_judge_prompt(career="產品經理(PM)", result=result)
    assert "產品經理(PM)" in prompt
    assert "行銷管理" in prompt
    assert "relevance" in prompt
    assert "1-5" in prompt
```

- [ ] **Step 2: Run tests to verify fail**

```bash
pytest tests/backend/test_judge.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement `backend/judge.py`**

```python
# backend/judge.py
from __future__ import annotations
import json
import math
from google import genai


def build_judge_prompt(career: str, result: dict) -> str:
    groups = result.get("groups", {})

    def format_group(name: str, courses: list[dict]) -> str:
        if not courses:
            return f"[{name}] （空）"
        lines = [f"[{name}]"]
        for c in courses:
            lines.append(f"  - {c.get('name', '?')} ({c.get('department', '?')}): {c.get('reason', '?')}")
        return "\n".join(lines)

    courses_text = "\n".join([
        format_group("核心技能", groups.get("core", [])),
        format_group("輔助技能", groups.get("supporting", [])),
        format_group("延伸視野", groups.get("extended", [])),
    ])

    return f"""你是大學課程推薦品質評審。請評估以下推薦結果的品質。

職涯目標：{career}

推薦課程：
{courses_text}

請從以下4個維度各給 1-5 分（1=很差，5=優秀）：

- relevance（相關性）：推薦課程與「{career}」職涯目標的整體相關程度
- grouping（分組品質）：核心/輔助/延伸三組的分類是否合理
- reason_quality（原因品質）：每門課的推薦原因是否具體、可幫助學生做決策
- diversity（多樣性）：推薦課程的系所、領域是否足夠多元，避免同質化

回傳 JSON（只回 JSON，不要其他文字）：
{{
  "relevance": <1-5>,
  "grouping": <1-5>,
  "reason_quality": <1-5>,
  "diversity": <1-5>,
  "critique": "<1-2句整體評語，指出最大優點和最需改進之處>"
}}"""


def parse_judge_response(raw: str) -> dict | None:
    """Parse LLM judge JSON response. Returns None if malformed."""
    try:
        data = json.loads(raw.strip())
        required = {"relevance", "grouping", "reason_quality", "diversity", "critique"}
        if not required.issubset(data.keys()):
            return None

        def clamp(v: int) -> int:
            return max(1, min(5, int(v)))

        r = clamp(data["relevance"])
        g = clamp(data["grouping"])
        rq = clamp(data["reason_quality"])
        d = clamp(data["diversity"])
        overall = round((r + g + rq + d) / 4)

        return {
            "judge_relevance_score": r,
            "judge_grouping_score": g,
            "judge_reason_score": rq,
            "judge_diversity_score": d,
            "judge_overall_score": overall,
            "judge_critique": str(data["critique"])[:500],  # cap length
        }
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None


async def evaluate_recommendation(
    client: genai.Client, career: str, result: dict
) -> dict | None:
    """Run LLM judge. Returns parsed scores or None on failure."""
    try:
        prompt = build_judge_prompt(career, result)
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        return parse_judge_response(resp.text)
    except Exception as e:
        print(f"[judge] Evaluation failed: {e}")
        return None
```

- [ ] **Step 4: Run tests — verify pass**

```bash
pytest tests/backend/test_judge.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/judge.py tests/backend/test_judge.py
git commit -m "feat(backend): LLM judge with 4-dimension quality scoring"
```

---

## Task 5: Wire Logging + Judge into `main.py`

**Files:**
- Modify: `backend/main.py`
- Modify: `backend/logger.py` (add `update_judge_scores` + `insert_log`)
- Modify: `backend/recommend.py` (add instrumented version)

- [ ] **Step 1: Add `update_judge_scores` and `insert_log` to `logger.py`**

```python
# backend/logger.py — append:

async def insert_log(pool, record: dict) -> int | None:
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
    if pool is None or not scores:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE query_log SET
                    judge_relevance_score=$1, judge_grouping_score=$2,\n                    judge_reason_score=$3, judge_diversity_score=$4,\n                    judge_overall_score=$5, judge_critique=$6,\n                    judge_evaluated_at=NOW()\n                WHERE id=$7""",
                scores["judge_relevance_score"], scores["judge_grouping_score"],
                scores["judge_reason_score"], scores["judge_diversity_score"],
                scores["judge_overall_score"], scores["judge_critique"], log_id,
            )
    except Exception as e:
        print(f"[logger] judge update failed for {log_id}: {e}")
```

- [ ] **Step 2: Add `build_recommendation_instrumented` to `recommend.py`**

```python
# backend/recommend.py — append:
def build_recommendation_instrumented(client, store_name, career):
    t0 = time.monotonic()
    skills = load_careers()[career]["skills"]
    meta = load_courses_meta()
    candidates = stage1_retrieve(client, store_name, career, skills)
    stage1_count = len(candidates)
    stage2 = stage2_group(client, career, skills, candidates)
    def process_group(items):
        raw = [{"course_id": i.course_id, "reason": i.reason} for i in items]
        return join_metadata(deduplicate_by_prefix(raw), meta)
    result = {
        "career": career,
        "groups": {
            "core": process_group(stage2.groups.core),
            "supporting": process_group(stage2.groups.supporting),
            "extended": process_group(stage2.groups.extended),
        },
        "latency_ms": int((time.monotonic() - t0) * 1000),
    }
    return result, stage1_count
```

- [ ] **Step 3: Replace `main.py` with two-phase logging version**

```python
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
_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
_STORE_NAME = os.environ["FILE_SEARCH_STORE_NAME"]
_ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")

@asynccontextmanager
async def lifespan(app):
    await init_pool()
    yield
    await close_pool()

app = FastAPI(title="NCCU Course Recommender", lifespan=lifespan)
app.add_middleware(CORSMiddleware,
    allow_origins=[_ALLOWED_ORIGIN] if _ALLOWED_ORIGIN != "*" else ["*"],
    allow_methods=["POST", "GET"], allow_headers=["Content-Type"])

async def _background_log_and_judge(career, result, stage1_count, error):
    pool = get_pool()
    record = build_log_record(career, result, stage1_count, error)
    log_id = await insert_log(pool, record)
    if log_id and result:
        scores = await evaluate_recommendation(_client, career, result)
        if scores:
            await update_judge_scores(pool, log_id, scores)

@app.get("/health")
def health(): return {"status": "ok"}

@app.post("/recommend", response_model=RecommendResponse)
async def recommend(req: RecommendRequest, background_tasks: BackgroundTasks):
    if req.career not in load_careers():
        raise HTTPException(400, detail=f"Unknown career: {req.career}")
    result = stage1_count = error = None
    try:
        result, stage1_count = build_recommendation_instrumented(_client, _STORE_NAME, req.career)
    except Exception as e:
        error = e
    background_tasks.add_task(_background_log_and_judge, req.career, result, stage1_count or 0, error)
    if error:
        raise HTTPException(503, detail=str(error))
    return result
```

- [ ] **Step 4: Run full test suite**

```bash
pytest tests/backend/ -v
```

Expected: All tests PASS.

- [ ] **Step 5: Smoke test — Phase 1 + Phase 2 verified**

```bash
cd backend && uvicorn main:app --port 8000
curl -s -X POST http://localhost:8000/recommend -H "Content-Type: application/json" -d '{"career": "資料科學家"}'
```

Wait 15s, then in Railway Postgres:
```sql
SELECT career, success, latency_ms, judge_overall_score, judge_critique, judge_evaluated_at
FROM query_log ORDER BY created_at DESC LIMIT 3;
```

Expected: `success=true`, `judge_overall_score` 1-5, `judge_critique` non-null.

- [ ] **Step 6: Commit**

```bash
git add backend/main.py backend/logger.py backend/recommend.py
git commit -m "feat(backend): two-phase logging with LLM judge quality scoring"
```

---

## Task 6: Product Analytics Queries

> Reference queries for product operations. Save in `docs/analytics.sql`.

- [ ] **Step 1: Create `docs/analytics.sql`**

```sql
-- Top 10 most queried careers
SELECT career, COUNT(*) as total, AVG(latency_ms) as avg_ms
FROM query_log
GROUP BY career
ORDER BY total DESC
LIMIT 10;

-- Error rate per career
SELECT career,
       COUNT(*) as total,
       SUM(CASE WHEN success THEN 0 ELSE 1 END) as errors,
       ROUND(100.0 * SUM(CASE WHEN success THEN 0 ELSE 1 END) / COUNT(*), 1) as error_pct
FROM query_log
GROUP BY career
ORDER BY error_pct DESC;

-- Error breakdown by type
SELECT error_type, COUNT(*) as count, MAX(created_at) as last_seen
FROM query_log
WHERE success = false
GROUP BY error_type
ORDER BY count DESC;

-- Latency percentiles (last 7 days)
SELECT
    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY latency_ms) as p50_ms,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) as p95_ms,
    PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY latency_ms) as p99_ms,
    AVG(latency_ms) as avg_ms,
    COUNT(*) as total
FROM query_log
WHERE created_at > NOW() - INTERVAL '7 days' AND success = true;

-- Stage1 low-hit careers (might need better skill mapping)
SELECT career, AVG(stage1_count) as avg_stage1_hits
FROM query_log
WHERE success = true
GROUP BY career
HAVING AVG(stage1_count) < 5
ORDER BY avg_stage1_hits;
```

- [ ] **Step 2: Commit**

```bash
git add docs/analytics.sql
git commit -m "docs: Postgres analytics queries for product operations"
```

---

## Task 7: Final Verification

- [ ] **Step 1: Run all tests**

```bash
pytest tests/ -v
```

Expected: All tests PASS.

- [ ] **Step 2: Verify `superpowers:verification-before-completion`**

```bash
# Make 3 requests, then check DB
for career in "產品經理(PM)" "資料科學家" "管理顧問"; do
  curl -s -X POST http://localhost:8000/recommend \
    -H "Content-Type: application/json" \
    -d "{\"career\": \"$career\"}" > /dev/null
done
```

In Railway Postgres:
```sql
SELECT career, success, latency_ms, result_core_count FROM query_log ORDER BY created_at DESC LIMIT 3;
```

Expected: 3 rows, all `success = true`.

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat(backend): complete Postgres logging with analytics queries"
```
