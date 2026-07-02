# tests/backend/test_dept_filter.py
"""Task 6: build_dept_filter（OpenAI vector store filter 組裝）+ extract_slots。

決定#2：使用者指定系所/學院卻沒指定學制時，預設排除通識課
（額外加一條 {"type":"ne","key":"degree_level","value":"通識"}）。
"""
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest

from backend.dept_query import build_dept_filter, extract_slots, normalize_degree


# ───────────────────────────── build_dept_filter ─────────────────────────────


def test_dept_only_excludes_ge_by_default():
    """department 有值、degree_level 未指定 → 加通識排除（決定#2）。"""
    f = build_dept_filter({"department": "歷史學系", "college": None, "degree_level": None})
    assert f == {
        "type": "and",
        "filters": [
            {"type": "eq", "key": "dept_canonical", "value": "歷史學系"},
            {"type": "ne", "key": "degree_level", "value": "通識"},
        ],
    }


def test_college_only_excludes_ge_by_default():
    """college 有值、degree_level 未指定 → 同樣加通識排除。"""
    f = build_dept_filter({"department": None, "college": "商學院", "degree_level": None})
    assert f == {
        "type": "and",
        "filters": [
            {"type": "eq", "key": "college", "value": "商學院"},
            {"type": "ne", "key": "degree_level", "value": "通識"},
        ],
    }


def test_dept_and_degree_masters_expands_in_no_ge_exclusion():
    """degree_level 已明指（碩士）→ 不加通識排除。"""
    f = build_dept_filter({"department": "歷史學系", "college": None, "degree_level": "碩士"})
    assert f == {
        "type": "and",
        "filters": [
            {"type": "eq", "key": "dept_canonical", "value": "歷史學系"},
            {"type": "in", "key": "degree_level", "value": ["碩士", "碩博"]},
        ],
    }


def test_degree_only_undergrad():
    """degree_level 是唯一 slot → 單一 clause，不受決定#2 影響（department/college 皆空）。"""
    f = build_dept_filter({"department": None, "college": None, "degree_level": "學士"})
    assert f == {"type": "eq", "key": "degree_level", "value": "學士"}


def test_graduate_expands_all():
    f = build_dept_filter({"department": None, "college": None, "degree_level": "研究所"})
    assert f == {"type": "in", "key": "degree_level", "value": ["碩士", "博士", "碩博"]}


def test_doctorate_expands_in():
    f = build_dept_filter({"department": None, "college": None, "degree_level": "博士"})
    assert f == {"type": "in", "key": "degree_level", "value": ["博士", "碩博"]}


def test_empty_returns_none():
    assert build_dept_filter({"department": None, "college": None, "degree_level": None}) is None


def test_dept_and_college_and_degree_all_set_no_ge_exclusion():
    """三個 slot 都有值 → and 三條 clause，degree_level 明指故不加通識排除。"""
    f = build_dept_filter({"department": "歷史學系", "college": "文學院", "degree_level": "學士"})
    assert f == {
        "type": "and",
        "filters": [
            {"type": "eq", "key": "dept_canonical", "value": "歷史學系"},
            {"type": "eq", "key": "college", "value": "文學院"},
            {"type": "eq", "key": "degree_level", "value": "學士"},
        ],
    }


def test_explicit_ge_degree_level_not_double_excluded():
    """使用者明確指定要通識（degree_level=通識）→ 誠實回 eq 通識，不自相矛盾加 ne 通識。"""
    f = build_dept_filter({"department": "歷史學系", "college": None, "degree_level": "通識"})
    assert f == {
        "type": "and",
        "filters": [
            {"type": "eq", "key": "dept_canonical", "value": "歷史學系"},
            {"type": "eq", "key": "degree_level", "value": "通識"},
        ],
    }


def test_general_education_department_query_not_self_contradictory():
    """Critical bug e2e（review 實測、已重現）："歷史系的通識課" → LLM 抽出 degree_level 原文
    「通識」→ normalize_degree("通識") 必須回 "通識"（非 None）→ build_dept_filter 必須產出
    eq 通識（使用者要的），不可落入「未指定學制」分支加 ne 通識（自相矛盾、排除掉使用者要的通識課）。
    這裡刻意經過 normalize_degree()（而非像 test_explicit_ge_degree_level_not_double_excluded
    直接硬編 "通識"），才會真正覆蓋到 normalize_degree 的 bug。
    """
    f = build_dept_filter(
        {"department": "歷史學系", "college": None, "degree_level": normalize_degree("通識")}
    )
    assert f == {
        "type": "and",
        "filters": [
            {"type": "eq", "key": "dept_canonical", "value": "歷史學系"},
            {"type": "eq", "key": "degree_level", "value": "通識"},
        ],
    }


# ───────────────────────────── extract_slots ─────────────────────────────


def _make_client(department=None, college=None, degree_level=None, raise_exc=None):
    """回傳一個 AsyncOpenAI-like 物件，responses.parse 回結構化 slots 或拋例外。"""
    if raise_exc is not None:
        async def _parse(**kwargs):
            raise raise_exc
    else:
        parsed = SimpleNamespace(department=department, college=college, degree_level=degree_level)
        result = SimpleNamespace(output_parsed=parsed)

        async def _parse(**kwargs):
            return result

    return SimpleNamespace(responses=SimpleNamespace(parse=_parse))


@pytest.mark.asyncio
async def test_extract_slots_normalizes_llm_raw_output(monkeypatch):
    """LLM 回原文（未正規化）→ extract_slots 要用 normalize_* 轉成 canonical 值。"""
    client = _make_client(department="歷史系", college=None, degree_level="大學部")
    monkeypatch.setattr("backend.openai_client.get_client", lambda: client)

    result = await extract_slots("推薦給我 10 門歷史系的課")
    assert result == {"department": "歷史學系", "college": None, "degree_level": "學士"}


@pytest.mark.asyncio
async def test_extract_slots_all_none_when_nothing_mentioned(monkeypatch):
    client = _make_client(department=None, college=None, degree_level=None)
    monkeypatch.setattr("backend.openai_client.get_client", lambda: client)

    result = await extract_slots("有什麼有趣的課")
    assert result == {"department": None, "college": None, "degree_level": None}


@pytest.mark.asyncio
async def test_extract_slots_exception_returns_all_none(monkeypatch):
    client = _make_client(raise_exc=RuntimeError("模擬 API 掛掉"))
    monkeypatch.setattr("backend.openai_client.get_client", lambda: client)

    result = await extract_slots("推薦給我 10 門歷史系的課")
    assert result == {"department": None, "college": None, "degree_level": None}


@pytest.mark.asyncio
async def test_extract_slots_no_client_returns_all_none(monkeypatch):
    """OPENAI_API_KEY 未設（get_client 回 None）→ 不崩，全 None。"""
    monkeypatch.setattr("backend.openai_client.get_client", lambda: None)

    result = await extract_slots("推薦給我 10 門歷史系的課")
    assert result == {"department": None, "college": None, "degree_level": None}
