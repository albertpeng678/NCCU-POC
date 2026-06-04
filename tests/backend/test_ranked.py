# tests/backend/test_ranked.py
"""扁平 ranked 標註：schema、annotate-pool async、build_ranked_courses 轉換。
全程 mock，不打真 Gemini。"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from backend.recommend import (
    _RankedItem,
    _RankedOutput,
    stage2_annotate_pool_async,
)


def test_ranked_item_schema_fields():
    item = _RankedItem(
        course_id="000211012",
        group="core",
        reason_lead="總述",
        reason_points=[{"term": "分析", "detail": "拆解問題"},
                       {"term": "建模", "detail": "量化決策"}],
    )
    assert item.course_id == "000211012"
    assert item.group == "core"
    assert item.reason_points[0].term == "分析"


def test_ranked_output_is_list_of_items():
    out = _RankedOutput(courses=[
        _RankedItem(course_id="000211012", group="core",
                    reason_lead="x", reason_points=[]),
    ])
    assert len(out.courses) == 1


def _fake_client(parsed):
    resp = SimpleNamespace(parsed=parsed)
    aio = SimpleNamespace(models=SimpleNamespace(
        generate_content=AsyncMock(return_value=resp)))
    return SimpleNamespace(aio=aio)


@pytest.mark.asyncio
async def test_annotate_pool_returns_parsed_ranked_output():
    parsed = _RankedOutput(courses=[
        _RankedItem(course_id="000211012", group="core",
                    reason_lead="總述",
                    reason_points=[{"term": "分析", "detail": "拆解"}]),
    ])
    client = _fake_client(parsed)
    candidates = [{"course_id": "000211012", "course_name": "政治學", "relevance": "x"}]
    out = await stage2_annotate_pool_async(client, "PM", ["分析"], candidates)
    assert out is parsed
    client.aio.models.generate_content.assert_awaited_once()


from backend.recommend import build_ranked_courses


def _meta(*ids_names):
    m = {}
    for cid, name in ids_names:
        m[cid] = {"name": name, "department": "系", "teacher": "師",
                  "credits": 3.0, "syllabus_url": f"https://x/{cid}"}
    return m


def _cands(*ids_names):
    return [{"course_id": cid, "course_name": name, "relevance": "x"}
            for cid, name in ids_names]


def _ranked(*triples):
    """triples: (course_id, group) → _RankedOutput（理由固定 2 points）。"""
    return _RankedOutput(courses=[
        _RankedItem(course_id=cid, group=grp, reason_lead="總述",
                    reason_points=[{"term": "分析", "detail": "拆解"},
                                   {"term": "建模", "detail": "量化"}])
        for cid, grp in triples
    ])


def test_build_ranked_every_course_has_group_and_rank():
    # D1：每課都有 group ∈ {core,supporting,extended} + rank
    meta = _meta(("000010011", "A"), ("000020011", "B"))
    cands = _cands(("000010011", "A"), ("000020011", "B"))
    ranked = _ranked(("000010011", "core"), ("000020011", "supporting"))
    out = build_ranked_courses(ranked, cands, meta)
    assert [c["rank"] for c in out] == [0, 1]
    assert {c["group"] for c in out} <= {"core", "supporting", "extended"}
    assert out[0]["group"] == "core"
    assert out[1]["group"] == "supporting"


def test_build_ranked_preserves_order_as_rank():
    # D2：rank 由高到低（保留 annotate 回傳順序），rank=index
    meta = _meta(("000010011", "A"), ("000020011", "B"), ("000030011", "C"))
    cands = _cands(("000010011", "A"), ("000020011", "B"), ("000030011", "C"))
    ranked = _ranked(("000030011", "core"), ("000010011", "core"), ("000020011", "extended"))
    out = build_ranked_courses(ranked, cands, meta)
    assert [c["name"] for c in out] == ["C", "A", "B"]
    assert [c["rank"] for c in out] == [0, 1, 2]


def test_build_ranked_join_metadata_drops_missing():
    # D3：缺 meta 且無法前綴復原 → 丟棄、不帶 None
    meta = _meta(("000010011", "A"))
    cands = _cands(("000010011", "A"))
    ranked = _ranked(("000010011", "core"), ("777888999", "core"))
    out = build_ranked_courses(ranked, cands, meta)
    assert len(out) == 1
    assert out[0]["name"] == "A"
    assert all(c.get("name") is not None for c in out)


def test_build_ranked_prefix_recovery():
    # stage2 抄錯後3碼（前6碼與 candidate 相同）→ 復原成 candidate 真實 id
    meta = _meta(("000010011", "A"))
    cands = _cands(("000010011", "A"))
    ranked = _ranked(("000010099", "core"))
    out = build_ranked_courses(ranked, cands, meta)
    assert len(out) == 1
    assert out[0]["course_id"] == "000010011"


def test_build_ranked_reason_shape_two_bold_terms():
    # D4：reason={lead, points:[{term,detail}]}、≥2 粗體 term
    meta = _meta(("000010011", "A"))
    cands = _cands(("000010011", "A"))
    ranked = _ranked(("000010011", "core"))
    out = build_ranked_courses(ranked, cands, meta)
    reason = out[0]["reason"]
    assert reason["lead"] == "總述"
    assert len(reason["points"]) >= 2
    assert reason["points"][0]["term"] == "分析"


def test_build_ranked_no_duplicate_id():
    # D5：標註後無重複 id（同 id 出現兩次 → 只留首次/前綴去重）
    meta = _meta(("000010011", "A"))
    cands = _cands(("000010011", "A"))
    ranked = _ranked(("000010011", "core"), ("000010011", "supporting"))
    out = build_ranked_courses(ranked, cands, meta)
    ids = [c["course_id"] for c in out]
    assert len(ids) == len(set(ids))
    assert len(out) == 1


def test_build_ranked_no_cross_listed_same_name():
    # D5：跨掛同名（同名不同 id）→ 去同名只留一筆
    meta = _meta(("000010011", "通識"), ("000099088", "通識"))
    cands = _cands(("000010011", "通識"), ("000099088", "通識"))
    ranked = _ranked(("000010011", "core"), ("000099088", "supporting"))
    out = build_ranked_courses(ranked, cands, meta)
    names = [c["name"] for c in out]
    assert names == ["通識"]


def test_build_ranked_course_has_full_fields():
    meta = _meta(("000010011", "A"))
    cands = _cands(("000010011", "A"))
    ranked = _ranked(("000010011", "core"))
    out = build_ranked_courses(ranked, cands, meta)
    c = out[0]
    for k in ("course_id", "name", "department", "teacher", "credits",
              "group", "reason", "syllabus_url", "rank"):
        assert k in c


from backend.models import Course, RecommendResponse


def test_course_model_flat_with_group_and_rank():
    c = Course(
        course_id="000010011", name="政治學", department="政治系",
        teacher="蔡中民", credits=3.0, group="core",
        reason={"lead": "x", "points": [{"term": "分析", "detail": "拆解"}]},
        syllabus_url="https://x/a", rank=0,
    )
    assert c.group == "core"
    assert c.rank == 0
    assert c.reason.points[0].term == "分析"


def test_recommend_response_flat_courses_and_batch_default():
    # D6：扁平 courses + batch_size 預設 10 + 回 seed
    resp = RecommendResponse(
        career="產品經理(PM)",
        courses=[Course(
            course_id="000010011", name="政治學", department="政治系",
            teacher="蔡", credits=3.0, group="core",
            reason={"lead": "x", "points": [{"term": "分析", "detail": "拆解"}]},
            syllabus_url="https://x/a", rank=0)],
        latency_ms=1200, seed=42,
    )
    assert resp.batch_size == 10
    assert resp.seed == 42
    assert resp.courses[0].group == "core"
    assert resp.notice is None
