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
