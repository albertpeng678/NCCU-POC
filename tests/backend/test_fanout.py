# tests/backend/test_fanout.py
"""fan-out 並行檢索：每技能一支 file_search、合併去重、容錯、池上限。
全程 mock async client，不打真 Gemini。"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from backend.recommend import (
    FANOUT_TOP_K,
    FANOUT_PER_SKILL,
    POOL_TARGET,
    fanout_query_skill_async,
)


def _fake_client(text):
    """client.aio.models.generate_content 回傳 .text=text 的假 client。"""
    resp = SimpleNamespace(text=text)
    aio = SimpleNamespace(models=SimpleNamespace(
        generate_content=AsyncMock(return_value=resp)))
    return SimpleNamespace(aio=aio)


def test_fanout_constants_have_expected_values():
    assert FANOUT_TOP_K == 8
    assert FANOUT_PER_SKILL == 5
    assert POOL_TARGET == 30


@pytest.mark.asyncio
async def test_fanout_query_skill_parses_array():
    client = _fake_client(
        '[{"course_id":"000211012","course_name":"政治學","relevance":"x"}]')
    out = await fanout_query_skill_async(client, "store", "PM", "分析")
    assert out[0]["course_id"] == "000211012"
    client.aio.models.generate_content.assert_awaited_once()


@pytest.mark.asyncio
async def test_fanout_query_skill_empty_text_returns_empty_list():
    client = _fake_client("沒有相關課程")
    out = await fanout_query_skill_async(client, "store", "PM", "分析")
    assert out == []


from backend.recommend import merge_fanout_results


def _meta_for(ids_names):
    m = {}
    for cid, name in ids_names:
        m[cid] = {"name": name, "department": "系", "teacher": "師",
                  "credits": 3.0, "syllabus_url": f"https://x/{cid}"}
    return m


def test_merge_round_robin_interleaves_sources():
    # 技能1 = [A0, A1]、技能2 = [B0, B1] → round-robin: A0, B0, A1, B1
    # 使用完全不同的6碼前綴，避免 deduplicate_by_prefix 碰撞
    s1 = [{"course_id": "000011001", "course_name": "A0", "relevance": "x"},
          {"course_id": "000022001", "course_name": "A1", "relevance": "x"}]
    s2 = [{"course_id": "000033001", "course_name": "B0", "relevance": "x"},
          {"course_id": "000044001", "course_name": "B1", "relevance": "x"}]
    meta = _meta_for([("000011001", "A0"), ("000022001", "A1"),
                      ("000033001", "B0"), ("000044001", "B1")])
    out = merge_fanout_results([s1, s2], meta)
    names = [c["course_name"] for c in out]
    assert names == ["A0", "B0", "A1", "B1"]


def test_merge_dedup_overlapping_id_keeps_first():
    # 兩技能都檢出同一 course_id → 只留首次（先到的技能1）
    s1 = [{"course_id": "000010011", "course_name": "A0", "relevance": "from1"}]
    s2 = [{"course_id": "000010011", "course_name": "A0", "relevance": "from2"}]
    meta = _meta_for([("000010011", "A0")])
    out = merge_fanout_results([s1, s2], meta)
    assert len(out) == 1
    assert out[0]["relevance"] == "from1"


def test_merge_dedup_same_name_different_id():
    # 跨掛同名課（同名不同 id）→ deduplicate_by_name 去同名，只留一筆
    s1 = [{"course_id": "000010011", "course_name": "通識", "relevance": "x"}]
    s2 = [{"course_id": "000099088", "course_name": "通識", "relevance": "y"}]
    meta = _meta_for([("000010011", "通識"), ("000099088", "通識")])
    out = merge_fanout_results([s1, s2], meta)
    assert len(out) == 1


def test_merge_dedup_by_prefix():
    # 前6碼相同（不同班次後3碼）→ deduplicate_by_prefix 收斂一筆
    s1 = [{"course_id": "000010011", "course_name": "微積分甲", "relevance": "x"}]
    s2 = [{"course_id": "000010022", "course_name": "微積分乙", "relevance": "y"}]
    meta = _meta_for([("000010011", "微積分甲"), ("000010022", "微積分乙")])
    out = merge_fanout_results([s1, s2], meta)
    assert len(out) == 1


def test_merge_corrects_wrong_id_via_name():
    # 抄錯一碼但 course_name 對得上 → correct_candidate_ids 修回真實 id
    s1 = [{"course_id": "000010019", "course_name": "政治學", "relevance": "x"}]
    meta = _meta_for([("000010011", "政治學")])
    out = merge_fanout_results([s1], meta)
    assert out[0]["course_id"] == "000010011"


def _unique_id(i: int) -> str:
    """Generate a 9-digit course_id with a unique 6-digit prefix for each i."""
    return f"{(i + 1) * 1000:06d}001"


def test_merge_caps_at_pool_target():
    # 合併 > POOL_TARGET(30) → 截斷前 30
    # 使用各自獨立6碼前綴，避免 deduplicate_by_prefix 誤縮
    big = [[{"course_id": _unique_id(i), "course_name": f"課{i}", "relevance": "x"}]
           for i in range(40)]  # 40 個單元素來源
    meta = _meta_for([(_unique_id(i), f"課{i}") for i in range(40)])
    out = merge_fanout_results(big, meta)
    assert len(out) == POOL_TARGET


def test_merge_pool_equal_target_keeps_all():
    sources = [[{"course_id": _unique_id(i), "course_name": f"課{i}", "relevance": "x"}]
               for i in range(POOL_TARGET)]
    meta = _meta_for([(_unique_id(i), f"課{i}") for i in range(POOL_TARGET)])
    out = merge_fanout_results(sources, meta)
    assert len(out) == POOL_TARGET


def test_merge_small_pool_no_padding():
    sources = [[{"course_id": "000010011", "course_name": "A", "relevance": "x"}]]
    meta = _meta_for([("000010011", "A")])
    out = merge_fanout_results(sources, meta)
    assert len(out) == 1  # 不補零、不報錯


from unittest.mock import patch
from backend.recommend import fanout_retrieve_async


def _ok(*ids_names):
    return [{"course_id": cid, "course_name": name, "relevance": "x"}
            for cid, name in ids_names]


@pytest.mark.asyncio
async def test_fanout_retrieve_merges_all_skills():
    meta = _meta_for([("000010011", "A"), ("000020011", "B")])
    side = {"分析": _ok(("000010011", "A")), "溝通": _ok(("000020011", "B"))}

    async def fake_query(client, store, career, skill):
        return side[skill]

    with patch("backend.recommend.fanout_query_skill_async", new=fake_query), \
         patch("backend.recommend.load_courses_meta", return_value=meta):
        out = await fanout_retrieve_async(object(), "store", "PM", ["分析", "溝通"])
    names = sorted(c["course_name"] for c in out)
    assert names == ["A", "B"]


@pytest.mark.asyncio
async def test_fanout_retrieve_one_skill_raises_others_survive():
    # B1：一支 raise（503/timeout）→ gather 不整體失敗、該支貢獻 []、其餘正常
    meta = _meta_for([("000020011", "B")])

    async def fake_query(client, store, career, skill):
        if skill == "分析":
            raise RuntimeError("503 overloaded")
        return _ok(("000020011", "B"))

    with patch("backend.recommend.fanout_query_skill_async", new=fake_query), \
         patch("backend.recommend.load_courses_meta", return_value=meta):
        out = await fanout_retrieve_async(object(), "store", "PM", ["分析", "溝通"])
    assert [c["course_name"] for c in out] == ["B"]


@pytest.mark.asyncio
async def test_fanout_retrieve_one_skill_empty_not_no_match():
    # B2：一支回空 → 池由其餘組成、不誤判 no_match（回非空）
    meta = _meta_for([("000020011", "B")])

    async def fake_query(client, store, career, skill):
        return [] if skill == "分析" else _ok(("000020011", "B"))

    with patch("backend.recommend.fanout_query_skill_async", new=fake_query), \
         patch("backend.recommend.load_courses_meta", return_value=meta):
        out = await fanout_retrieve_async(object(), "store", "PM", ["分析", "溝通"])
    assert len(out) == 1


@pytest.mark.asyncio
async def test_fanout_retrieve_all_empty_returns_empty_pool():
    # B3：全部皆空 → 空池（上層才轉 no_match/error）
    async def fake_query(client, store, career, skill):
        return []

    with patch("backend.recommend.fanout_query_skill_async", new=fake_query), \
         patch("backend.recommend.load_courses_meta", return_value={}):
        out = await fanout_retrieve_async(object(), "store", "PM", ["分析", "溝通"])
    assert out == []


@pytest.mark.asyncio
async def test_fanout_retrieve_all_raise_returns_empty_pool():
    # B3 變體：全部 raise → 空池、不向外拋
    async def fake_query(client, store, career, skill):
        raise RuntimeError("boom")

    with patch("backend.recommend.fanout_query_skill_async", new=fake_query), \
         patch("backend.recommend.load_courses_meta", return_value={}):
        out = await fanout_retrieve_async(object(), "store", "PM", ["分析", "溝通"])
    assert out == []
