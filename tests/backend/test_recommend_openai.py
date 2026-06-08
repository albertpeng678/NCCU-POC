# tests/backend/test_recommend_openai.py
"""
Phase 1 OpenAI 遷移：fanout 用 search_skill、stage2 用 responses.parse、
derive_skills 用 _openai_structured。全程 mock，不打真 API。
TDD RED phase：在實作前先跑，全部應 FAIL。
"""
import pytest
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, call
from pydantic import BaseModel
from backend.recommend import fanout_query_skill_async


# ── 1. REC_SCORE_THRESHOLD 常數存在且為 float ─────────────────────────────────

def test_rec_score_threshold_constant_exists():
    from backend.recommend import REC_SCORE_THRESHOLD
    assert isinstance(REC_SCORE_THRESHOLD, float)
    assert REC_SCORE_THRESHOLD == 0.0


# ── 2. OPENAI_MODEL 常數存在且預設值正確 ─────────────────────────────────────

def test_openai_model_constant_default():
    """OPENAI_MODEL 從環境變數讀取，預設 gpt-5.4-mini。"""
    import importlib, backend.recommend as R
    # 記錄原值
    orig = getattr(R, "OPENAI_MODEL", None)
    assert orig is not None, "OPENAI_MODEL should exist in recommend.py"
    # 預設值驗證（在無 env override 的情況下）
    env_val = os.environ.get("OPENAI_MODEL", "gpt-5.4-mini")
    assert R.OPENAI_MODEL == env_val


# ── 3. fanout_query_skill_async 呼叫 search_skill (不再呼叫 genai generate) ──

@pytest.mark.asyncio
async def test_fanout_query_skill_calls_search_skill():
    """fanout_query_skill_async 應呼叫 search_skill，不再呼叫 client.aio.models.generate_content。"""
    from backend.recommend import fanout_query_skill_async, FANOUT_TOP_K, REC_SCORE_THRESHOLD

    fake_results = [
        {"course_id": "000211012", "score": 0.9, "content": "政治學課綱"},
    ]
    mock_client = MagicMock()  # OpenAI client

    with patch("backend.recommend.search_skill", new=AsyncMock(return_value=fake_results)) as mock_ss:
        out = await fanout_query_skill_async(mock_client, "vs_abc", "PM", "分析")

    mock_ss.assert_awaited_once_with(
        mock_client, "vs_abc", "分析",
        top_k=FANOUT_TOP_K,
        score_threshold=REC_SCORE_THRESHOLD,
    )
    # 回傳的候選包含 course_id、course_name 從 content 衍生或保留 content
    assert out[0]["course_id"] == "000211012"


@pytest.mark.asyncio
async def test_fanout_query_skill_empty_results_returns_empty():
    """search_skill 回 [] → fanout 也回 []。"""
    mock_client = MagicMock()

    with patch("backend.recommend.search_skill", new=AsyncMock(return_value=[])):
        out = await fanout_query_skill_async(mock_client, "vs_abc", "PM", "分析")

    assert out == []


@pytest.mark.asyncio
async def test_fanout_query_skill_content_used_as_course_name():
    """search_skill 的 content 欄位應出現在候選字典中（course_name 或 content 均可）。"""
    from backend.recommend import fanout_query_skill_async
    fake_results = [
        {"course_id": "070415001", "score": 0.85, "content": "資料科學概論"},
    ]
    mock_client = MagicMock()

    with patch("backend.recommend.search_skill", new=AsyncMock(return_value=fake_results)):
        out = await fanout_query_skill_async(mock_client, "vs_1", "DS", "資料分析")

    assert len(out) == 1
    c = out[0]
    assert c["course_id"] == "070415001"
    # course_name 或 content 欄位應有內容
    assert c.get("course_name") or c.get("content")


# ── 4. _openai_structured helper 存在且可呼叫 ────────────────────────────────

@pytest.mark.asyncio
async def test_openai_structured_calls_responses_parse():
    """_openai_structured(client, system, user, schema_model) 應呼叫 client.responses.parse
    並回傳 resp.output_parsed。
    """
    from backend.recommend import _openai_structured, OPENAI_MODEL

    class _TestSchema(BaseModel):
        value: str

    parsed_obj = _TestSchema(value="hello")
    mock_resp = SimpleNamespace(output_parsed=parsed_obj)
    mock_client = MagicMock()
    mock_client.responses.parse = AsyncMock(return_value=mock_resp)

    result = await _openai_structured(
        mock_client, "system msg", "user msg", _TestSchema
    )

    mock_client.responses.parse.assert_awaited_once_with(
        model=OPENAI_MODEL,
        input=[
            {"role": "system", "content": "system msg"},
            {"role": "user", "content": "user msg"},
        ],
        text_format=_TestSchema,
    )
    assert result is parsed_obj


# ── 5. stage2_annotate_pool_async 使用 _openai_structured ────────────────────

@pytest.mark.asyncio
async def test_stage2_annotate_pool_uses_openai_structured():
    """stage2_annotate_pool_async 應呼叫 _openai_structured，回傳 _RankedOutput。"""
    from backend.recommend import stage2_annotate_pool_async, _RankedOutput

    ranked = _RankedOutput(courses=[])
    mock_client = MagicMock()

    with patch("backend.recommend._openai_structured", new=AsyncMock(return_value=ranked)) as mock_fn:
        result = await stage2_annotate_pool_async(mock_client, "PM", ["分析"], [])

    mock_fn.assert_awaited_once()
    # 第一個位置參數是 client
    args = mock_fn.call_args[0]
    assert args[0] is mock_client
    assert result is ranked


# ── 6. derive_skills_for_career_async 使用 _openai_structured ────────────────

@pytest.mark.asyncio
async def test_derive_skills_async_uses_openai_structured():
    """derive_skills_for_career_async 應呼叫 _openai_structured，解析 skills 列表。"""
    from backend.recommend import derive_skills_for_career_async

    class _FakeOutput(BaseModel):
        skills: list

    fake_output = _FakeOutput(skills=["公共衛生", "基礎管理", "人際溝通"])
    mock_client = MagicMock()

    with patch("backend.recommend._openai_structured", new=AsyncMock(return_value=fake_output)) as mock_fn:
        skills = await derive_skills_for_career_async(mock_client, "護理師")

    mock_fn.assert_awaited_once()
    assert skills == ["公共衛生", "基礎管理", "人際溝通"]


@pytest.mark.asyncio
async def test_derive_skills_async_empty_returns_none():
    """_openai_structured 回 skills=[] → derive 回 None（no_match）。"""
    from backend.recommend import derive_skills_for_career_async

    class _FakeOutput(BaseModel):
        skills: list

    fake_output = _FakeOutput(skills=[])
    mock_client = MagicMock()

    with patch("backend.recommend._openai_structured", new=AsyncMock(return_value=fake_output)):
        skills = await derive_skills_for_career_async(mock_client, "asdfqwer")

    assert skills is None


# ── 7. /health 回應包含 retrieval_backend 與 model ────────────────────────────

def test_health_has_retrieval_backend_and_model():
    """GET /health 應包含 retrieval_backend='openai' 和 model=OPENAI_MODEL，保留 qa_mode。"""
    from unittest.mock import AsyncMock, patch
    from fastapi.testclient import TestClient

    with patch("backend.main.build_recommendation_instrumented_async", new_callable=AsyncMock):
        from backend.main import app, _QA_MODE
        tc = TestClient(app)

    resp = tc.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["retrieval_backend"] == "openai"
    assert "model" in data
    assert "qa_mode" in data
    assert data["qa_mode"] == _QA_MODE
