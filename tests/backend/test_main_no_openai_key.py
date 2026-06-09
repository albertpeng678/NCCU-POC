# tests/backend/test_main_no_openai_key.py
"""
Fix 2 + 3: Verify that when OPENAI_API_KEY is absent the server still starts
(/health returns 200) and OpenAI-dependent endpoints return 503 instead of
crashing with AttributeError or KeyError.
"""
from __future__ import annotations
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient


@pytest.fixture
def client_no_openai():
    """TestClient simulating missing OPENAI_API_KEY.

    Patch BOTH the cached client and the factory to None: the guard lazy-inits
    via the factory, so simulating "no key" means the factory must yield None too
    (otherwise conftest's dummy key would let it build a client).
    """
    with patch("backend.main._openai_client", None), \
         patch("backend.main._get_openai_client", return_value=None):
        from backend.main import app
        yield TestClient(app)


def test_health_ok_without_openai_key(client_no_openai):
    resp = client_no_openai.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_recommend_503_without_openai_key(client_no_openai):
    resp = client_no_openai.post("/recommend", json={"career": "產品經理(PM)"})
    assert resp.status_code == 503
    assert "OpenAI" in resp.json()["detail"]


def test_recommend_stream_503_without_openai_key(client_no_openai):
    resp = client_no_openai.get("/recommend/stream?career=產品經理(PM)")
    assert resp.status_code == 503
