#!/usr/bin/env python3
"""離線預算：對 50 固定職涯各跑一次完整推薦 pipeline，把整池結果存進 career_budget 表。
線上命中即秒出（0 即時 AI）。低併發避開 embedding-001 區域限流；冪等可重跑、可只補缺。

用法（env）：
  OPENAI_API_KEY=...  OPENAI_VECTOR_STORE_ID=...  DATABASE_URL=<postgres>
  CONCURRENCY=2            # 預設 2，避免放大 embedding 429
  ONLY_MISSING=1          # 只補尚未預算的職涯（冪等增量）
  ONLY="產品經理(PM)"      # 只跑單一職涯（驗證/重補用）
  python scripts/build_career_budget.py
"""
import os
import sys
import asyncio
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import asyncpg
from openai import AsyncOpenAI

from backend.recommend import load_careers, stream_recommendation
from backend.career_budget import upsert_budget

_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4-mini")


def select_careers_to_build(all_careers, existing, only=None, only_missing=False):
    """決定要算哪些職涯：only 優先（單一）；否則全部，only_missing 時排除已有。"""
    if only:
        return [only] if only in all_careers else []
    base = list(all_careers)
    if only_missing:
        base = [c for c in base if c not in existing]
    return base


async def _build_one(client, store, career):
    """跑 stream_recommendation 收 result event → 回扁平 courses（error/no_match → None 跳過）。"""
    result = None
    async for ev in stream_recommendation(client, store, career, seed=0, skills=None):
        if ev["event"] == "result":
            result = ev["data"]
        elif ev["event"] in ("error", "no_match"):
            return None
    return (result or {}).get("courses")


async def main():
    api_key = os.environ["OPENAI_API_KEY"]
    store = os.environ["OPENAI_VECTOR_STORE_ID"]
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not db_url:
        sys.exit("需要 DATABASE_URL（或 DATABASE_PUBLIC_URL）才能寫 career_budget")
    concurrency = int(os.environ.get("CONCURRENCY", "2"))
    only = os.environ.get("ONLY") or None
    only_missing = os.environ.get("ONLY_MISSING", "") not in ("", "0", "false")

    # OpenAI client：SDK 內建退避重試（429/503）；同一 event loop 內用。
    client = AsyncOpenAI(api_key=api_key, max_retries=5, timeout=180.0)
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=max(concurrency, 2))

    # 確保表存在（冪等）
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS career_budget (
                career VARCHAR(120) PRIMARY KEY, payload_json JSONB NOT NULL,
                pool_size INTEGER NOT NULL, model VARCHAR(40) NOT NULL,
                seed INTEGER NOT NULL DEFAULT 0, built_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""")
        rows = await conn.fetch("SELECT career FROM career_budget")
    existing = {r["career"] for r in rows}

    careers = load_careers()
    if only and only not in careers:
        sys.exit(f"[budget] ONLY={only!r} 不在 50 職涯清單中（打錯字？）→ 不做任何事")
    targets = select_careers_to_build(list(careers.keys()), existing, only, only_missing)
    print(f"[budget] 既有 {len(existing)} / 目標 {len(targets)} 個職涯 · 併發 {concurrency}")

    sem = asyncio.Semaphore(concurrency)
    done = {"ok": 0, "fail": 0}
    failed = []
    t0 = time.time()

    async def worker(career):
        async with sem:
            try:
                courses = await _build_one(client, store, career)
                if not courses:
                    raise RuntimeError("空結果（檢索空/no_match）")
                ok = await upsert_budget(pool, career, courses, _MODEL, 0)
                if not ok:
                    raise RuntimeError("upsert 失敗")
                done["ok"] += 1
                print(f"  ✓ {career}  ({len(courses)} 課)  [{done['ok']+done['fail']}/{len(targets)}]")
            except Exception as e:
                done["fail"] += 1
                failed.append(career)
                print(f"  ✗ {career}  {str(e)[:80]}")

    await asyncio.gather(*(worker(c) for c in targets))
    await pool.close()
    dt = time.time() - t0
    print(f"\n[budget] 完成 ok={done['ok']} fail={done['fail']} · {dt:.0f}s")
    if failed:
        print(f"[budget] 失敗清單（可 ONLY 重補）：{failed}")


if __name__ == "__main__":
    asyncio.run(main())
