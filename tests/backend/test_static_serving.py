# tests/backend/test_static_serving.py
"""同源部署：backend 直接服務 frontend 靜態檔（StaticFiles mount 於 "/"），
且 API 路由（/health 等）優先於 catch-all static mount。
"""
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)


def test_root_serves_frontend_index():
    r = client.get("/")
    assert r.status_code == 200
    assert "NCCU" in r.text  # index.html 標題含 NCCU


def test_static_app_js_served():
    r = client.get("/app.js")
    assert r.status_code == 200
    assert "CONFIG" in r.text  # app.js 內容


def test_static_pagination_js_served():
    # ES module 依賴：缺了線上會 404 → 整頁空白
    r = client.get("/pagination.js")
    assert r.status_code == 200
    assert "groupBatch" in r.text


def test_health_api_takes_precedence_over_static():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
