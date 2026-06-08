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
async def test_fanout_query_skill_course_name_from_meta_not_chunk():
    """fanout 的 course_name 應從 courses_meta.json 查真實短課名，不得為空字串（否則 dedup_by_name 全去重）
    也不得為 chunk 全文（否則灌爆 stage2 prompt）。

    若 course_id 在 meta 中存在 → course_name 為真實短課名（非空、非 chunk 內容）。
    若 course_id 查不到 meta → course_name 為 ""（join_metadata 後續會過濾，可接受）。
    """
    from backend.recommend import fanout_query_skill_async

    fake_results = [
        {"course_id": "070415001", "score": 0.85, "content": "很長的課綱全文..." * 50},
    ]
    mock_client = MagicMock()

    with patch("backend.recommend.search_skill", new=AsyncMock(return_value=fake_results)):
        out = await fanout_query_skill_async(mock_client, "vs_1", "DS", "資料分析")

    assert len(out) == 1
    c = out[0]
    assert c["course_id"] == "070415001"
    # course_name 不得是 chunk 全文（防 stage2 prompt 爆炸）
    assert len(c["course_name"]) < 100, f"course_name 過長（疑為 chunk 全文）: {c['course_name'][:100]}"
    # course_name 不得是空字串（防 dedup_by_name 全去重 regression）
    # 070415001 在 courses_meta.json 中存在 → 應有真實短課名
    assert c["course_name"] != "", (
        "course_name 不應為空字串：空 name 會讓 deduplicate_by_name 把所有課視為同名 → 合併池塌成 1 門"
    )


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
    from backend.recommend import derive_skills_for_career_async, _DerivedSkills

    fake_output = _DerivedSkills(is_legitimate_career=True, skills=["公共衛生", "基礎管理", "人際溝通"])
    mock_client = MagicMock()

    with patch("backend.recommend._openai_structured", new=AsyncMock(return_value=fake_output)) as mock_fn:
        skills = await derive_skills_for_career_async(mock_client, "護理師")

    mock_fn.assert_awaited_once()
    assert skills == ["公共衛生", "基礎管理", "人際溝通"]


@pytest.mark.asyncio
async def test_derive_skills_async_empty_returns_none():
    """_openai_structured 回 skills=[] → derive 回 None（no_match）。"""
    from backend.recommend import derive_skills_for_career_async, _DerivedSkills

    fake_output = _DerivedSkills(is_legitimate_career=False, skills=[])
    mock_client = MagicMock()

    with patch("backend.recommend._openai_structured", new=AsyncMock(return_value=fake_output)):
        skills = await derive_skills_for_career_async(mock_client, "asdfqwer")

    assert skills is None


# ── Fix A：_openai_structured output_parsed=None → ValueError ────────────────

@pytest.mark.asyncio
async def test_openai_structured_none_output_raises_valueerror():
    """responses.parse 回 output_parsed=None（refusal/token-limit）→ 應 raise ValueError，不回 None。"""
    from backend.recommend import _openai_structured, OPENAI_MODEL
    from pydantic import BaseModel as BM

    class _TestSchema(BM):
        value: str

    mock_resp = SimpleNamespace(output_parsed=None, output_text="I cannot comply.")
    mock_client = MagicMock()
    mock_client.responses.parse = AsyncMock(return_value=mock_resp)

    with pytest.raises(ValueError, match="structured output None"):
        await _openai_structured(mock_client, "sys", "usr", _TestSchema)


@pytest.mark.asyncio
async def test_stage2_annotate_pool_raises_when_openai_structured_returns_none():
    """stage2_annotate_pool_async 呼叫 _openai_structured 時若 output_parsed=None → 拋 ValueError（非回 None）。"""
    from backend.recommend import stage2_annotate_pool_async
    from types import SimpleNamespace

    mock_resp = SimpleNamespace(output_parsed=None, output_text="refusal")
    mock_client = MagicMock()
    mock_client.responses.parse = AsyncMock(return_value=mock_resp)

    candidates = [{"course_id": "000211012", "course_name": "政治學", "relevance": "x"}]
    with pytest.raises(ValueError, match="structured output None"):
        await stage2_annotate_pool_async(mock_client, "PM", ["分析"], candidates)


# ── Fix D：fanout_query_skill_async 依 course_id 去重 ─────────────────────────

@pytest.mark.asyncio
async def test_fanout_query_skill_deduplicates_by_course_id():
    """search_skill 回同 course_id 的多個 chunk → fanout 應去重，只保留第一筆，保序。"""
    from backend.recommend import fanout_query_skill_async

    # 同一 course_id 出現兩次（不同 chunk），加上另一門課
    fake_results = [
        {"course_id": "702744001", "score": 0.95, "content": "chunk1"},
        {"course_id": "702744001", "score": 0.85, "content": "chunk2"},  # 重複
        {"course_id": "652150001", "score": 0.80, "content": "chunk3"},
    ]
    mock_client = MagicMock()

    with patch("backend.recommend.search_skill", new=AsyncMock(return_value=fake_results)):
        out = await fanout_query_skill_async(mock_client, "vs_1", "PM", "分析")

    assert len(out) == 2
    assert out[0]["course_id"] == "702744001"
    assert out[1]["course_id"] == "652150001"


# ── Regression：fanout 合併池不應因空 course_name 塌成 1 門 ────────────────────
# 根因：course_name="" → deduplicate_by_name 視所有課為同名 → 全部去重 → 只剩 1 筆。
# 修法：course_name 應從 courses_meta.json 查真實短課名，而非留空字串。

@pytest.mark.asyncio
async def test_fanout_retrieve_pool_not_collapsed_by_empty_course_name():
    """Regression：多技能不同 course_id → 合併池數量應 == 各技能不重疊 id 總和，不因空 name 塌成 1。

    技能A 回 [000211012（政治學）, 000216001（社會學）]
    技能B 回 [000217012（個體經濟學）]
    三門課 course_id 不重疊 → 合併池應有 3 門，不得 < 3。
    """
    from backend.recommend import fanout_retrieve_async

    # 使用 courses_meta.json 中確定存在的真實 course_id
    skill_a_results = [
        {"course_id": "000211012", "score": 0.9, "content": "政治學課綱"},
        {"course_id": "000216001", "score": 0.85, "content": "社會學課綱"},
    ]
    skill_b_results = [
        {"course_id": "000217012", "score": 0.88, "content": "個體經濟學課綱"},
    ]

    async def fake_search_skill(client, vs_id, query, top_k, score_threshold):
        if query == "技能A":
            return skill_a_results
        elif query == "技能B":
            return skill_b_results
        return []

    with patch("backend.recommend.search_skill", new=AsyncMock(side_effect=fake_search_skill)):
        pool = await fanout_retrieve_async(
            object(), "vs_test", "職涯", ["技能A", "技能B"]
        )

    pool_ids = {c["course_id"] for c in pool}
    assert len(pool) == 3, (
        f"合併池應有 3 門（regression：空 course_name 造成 deduplicate_by_name 全去重），"
        f"實際得到 {len(pool)} 門：{pool_ids}"
    )
    assert "000211012" in pool_ids
    assert "000216001" in pool_ids
    assert "000217012" in pool_ids


# ── 7. derive_skills_for_career_async：is_legitimate_career 守門 ───────────────
# RED 階段：先跑 FAIL，實作 _DerivedSkills 加 is_legitimate_career 欄位 + 判斷邏輯後轉 GREEN。

@pytest.mark.asyncio
async def test_derive_skills_legitimate_career_returns_skills():
    """is_legitimate_career=True, skills 非空 → 正常回 skills 清單（不誤殺正當清單外職涯）。"""
    from backend.recommend import derive_skills_for_career_async, _DerivedSkills

    fake_output = _DerivedSkills(is_legitimate_career=True, skills=["採訪", "查證"])
    mock_client = MagicMock()

    with patch("backend.recommend._openai_structured", new=AsyncMock(return_value=fake_output)):
        skills = await derive_skills_for_career_async(mock_client, "記者")

    assert skills == ["採訪", "查證"]


@pytest.mark.asyncio
async def test_derive_skills_illegitimate_empty_skills_returns_none():
    """is_legitimate_career=False, skills=[]（非職業，如「流浪漢」）→ 回 None（走 no_match）。"""
    from backend.recommend import derive_skills_for_career_async, _DerivedSkills

    fake_output = _DerivedSkills(is_legitimate_career=False, skills=[])
    mock_client = MagicMock()

    with patch("backend.recommend._openai_structured", new=AsyncMock(return_value=fake_output)):
        result = await derive_skills_for_career_async(mock_client, "流浪漢")

    assert result is None


@pytest.mark.asyncio
async def test_derive_skills_illegitimate_with_skills_returns_none():
    """is_legitimate_career=False, 但 skills 非空（如「黑道老大」模型仍給技能）→ 回 None。
    is_legitimate_career 優先於 skills：不應讓非正當輸入繞過守門。
    """
    from backend.recommend import derive_skills_for_career_async, _DerivedSkills

    fake_output = _DerivedSkills(is_legitimate_career=False, skills=["談判", "領導"])
    mock_client = MagicMock()

    with patch("backend.recommend._openai_structured", new=AsyncMock(return_value=fake_output)):
        result = await derive_skills_for_career_async(mock_client, "黑道老大")

    assert result is None


@pytest.mark.asyncio
async def test_derive_skills_exception_returns_none():
    """_openai_structured 拋例外 → 回 None（不拖垮）。"""
    from backend.recommend import derive_skills_for_career_async

    mock_client = MagicMock()

    with patch("backend.recommend._openai_structured", new=AsyncMock(side_effect=ValueError("boom"))):
        result = await derive_skills_for_career_async(mock_client, "任意輸入")

    assert result is None


# ── 8. /health 回應包含 retrieval_backend 與 model ────────────────────────────

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
