# tests/backend/test_career_budget.py
"""career_budget：50 固定職涯離線預算的整池序列化/還原 + DB 讀寫（命中/未命中/無 DB 降級）。
純函式 round-trip 可單測；DB I/O 用 fake pool 不連真 DB。
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.career_budget import serialize_pool, deserialize_pool, get_budget, upsert_budget


_COURSES = [
    {"course_id": "070415001", "name": "資料科學基礎", "department": "社科院", "teacher": "杜福童",
     "credits": 3.0, "group": "core", "rank": 1, "syllabus_url": "http://x/1",
     "reason": {"lead": "很適合", "points": [{"term": "統計", "detail": "打底"}]}},
    {"course_id": "070415002", "name": "機器學習", "department": "資科系", "teacher": "李四",
     "credits": 3.0, "group": "extend", "rank": 2, "syllabus_url": "http://x/2",
     "reason": {"lead": "進階", "points": []}},
]


# ---------- 純函式 round-trip ----------

def test_serialize_deserialize_roundtrip():
    payload = serialize_pool(_COURSES)
    back = deserialize_pool(payload)
    assert back == _COURSES


def test_deserialize_empty():
    assert deserialize_pool([]) == []


def test_deserialize_fills_group_and_reason_defaults():
    # payload 形狀漂移（缺 group/reason）→ 補安全預設，前端不崩
    back = deserialize_pool([{"course_id": "x", "name": "課"}])
    assert back[0]["group"] == "core"
    assert back[0]["reason"] == {"lead": "", "points": []}


def test_deserialize_skips_courses_missing_id_or_name():
    back = deserialize_pool([{"course_id": "", "name": "課"}, {"name": "無id"}, {"course_id": "ok", "name": "好"}])
    assert [c["course_id"] for c in back] == ["ok"]


def test_deserialize_ignores_unknown_keys_and_fills_rank():
    payload = [{"course_id": "x", "name": "課", "extra_junk": 1}]
    back = deserialize_pool(payload)
    assert back[0]["course_id"] == "x"
    assert "extra_junk" not in back[0]
    assert back[0].get("rank") == 0   # 缺 rank 補預設


# ---------- DB 讀寫（fake pool）----------

def _fake_pool(fetchrow_result):
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_result)
    conn.execute = AsyncMock()
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool, conn


@pytest.mark.asyncio
async def test_get_budget_hit_returns_courses():
    row = {"payload_json": json.dumps(serialize_pool(_COURSES)), "pool_size": 2, "built_at": "2026-06-08"}
    pool, _ = _fake_pool(row)
    res = await get_budget(pool, "資料科學家")
    assert res is not None
    assert [c["course_id"] for c in res["courses"]] == ["070415001", "070415002"]
    assert res["pool_size"] == 2


@pytest.mark.asyncio
async def test_get_budget_miss_returns_none():
    pool, _ = _fake_pool(None)
    assert await get_budget(pool, "不存在") is None


@pytest.mark.asyncio
async def test_get_budget_no_pool_returns_none():
    assert await get_budget(None, "資料科學家") is None


@pytest.mark.asyncio
async def test_get_budget_swallows_db_error():
    pool, conn = _fake_pool(None)
    conn.fetchrow = AsyncMock(side_effect=RuntimeError("db down"))
    assert await get_budget(pool, "資料科學家") is None


@pytest.mark.asyncio
async def test_upsert_budget_executes_with_pool_size():
    pool, conn = _fake_pool(None)
    ok = await upsert_budget(pool, "資料科學家", _COURSES, "gemini-2.5-flash", 0)
    assert ok is True
    conn.execute.assert_awaited()
    args = conn.execute.await_args.args
    assert "資料科學家" in args        # career 有傳
    assert 2 in args                   # pool_size = len(courses)


@pytest.mark.asyncio
async def test_upsert_budget_no_pool_noop():
    assert await upsert_budget(None, "x", _COURSES, "m", 0) is False
