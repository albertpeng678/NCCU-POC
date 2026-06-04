# tests/backend/test_db_resilience.py
"""init_pool 韌性：DATABASE_URL 連不到 Postgres 時，不可 raise 崩潰啟動，
應優雅降級（_pool=None、app 照常啟動、/health 仍回）。對齊 Q&A ephemeral 降級。
"""
import pytest
from backend import db


@pytest.fixture(autouse=True)
def _reset_pool():
    db._pool = None
    yield
    db._pool = None


@pytest.mark.asyncio
async def test_init_pool_no_url_skips(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    await db.init_pool()
    assert db.get_pool() is None


@pytest.mark.asyncio
async def test_init_pool_connection_failure_does_not_crash(monkeypatch):
    """DATABASE_URL 有設但連線失敗 → init_pool 不 raise、_pool 維持 None。"""
    monkeypatch.setenv("DATABASE_URL", "postgresql://bad:bad@127.0.0.1:1/none")

    async def _boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(db.asyncpg, "create_pool", _boom)
    # 關鍵斷言：不應拋出例外（否則 lifespan 崩潰 → healthcheck 失敗）
    await db.init_pool()
    assert db.get_pool() is None


@pytest.mark.asyncio
async def test_init_pool_success_sets_pool(monkeypatch):
    """DATABASE_URL 有設且連線成功 → _pool 被設定（回歸保護）。"""
    sentinel = object()
    monkeypatch.setenv("DATABASE_URL", "postgresql://ok:ok@127.0.0.1:5432/db")

    async def _ok(*a, **k):
        return sentinel

    monkeypatch.setattr(db.asyncpg, "create_pool", _ok)
    await db.init_pool()
    assert db.get_pool() is sentinel
