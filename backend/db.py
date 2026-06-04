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
    try:
        # timeout=10：連不到時快速失敗（勿 hang 過 healthcheck 窗口）→ 走下方 except 降級。
        _pool = await asyncpg.create_pool(
            database_url, min_size=1, max_size=5, timeout=10
        )
        print("[db] Postgres pool initialized")
    except Exception as e:
        # 連不到 Postgres 不可崩潰啟動（否則 lifespan 失敗 → healthcheck 掛 → 整個服務不可用）。
        # 優雅降級：_pool 維持 None，logging/judge 跳過、Q&A 走 ephemeral session。
        _pool = None
        print(f"[db] Postgres unavailable — degrading to no-DB mode: {e}")


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def get_pool():
    return _pool
