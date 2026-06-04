from unittest.mock import MagicMock
from backend.recommend import derive_skills_for_career


def _client_returning(text):
    c = MagicMock()
    c.models.generate_content.return_value = MagicMock(text=text)
    return c


def test_derive_skills_parses_list():
    c = _client_returning('["衛生管理","公共衛生","基礎管理","人際溝通","職場安全"]')
    skills = derive_skills_for_career(c, "清潔工")
    assert "公共衛生" in skills and len(skills) >= 3


def test_derive_skills_none_for_garbage():
    c = _client_returning('[]')
    assert derive_skills_for_career(c, "asdfqwer") is None


def test_build_uses_injected_skills(monkeypatch):
    import asyncio
    import backend.recommend as R
    captured = {}

    async def fake_fanout(client, store, career, skills):
        captured["skills"] = skills
        return [{"course_id": "000211012", "course_name": "X", "relevance": "r"}]

    async def fake_annotate(client, career, skills, candidates):
        from backend.recommend import _RankedOutput
        return _RankedOutput(courses=[])

    monkeypatch.setattr(R, "fanout_retrieve_async", fake_fanout)
    monkeypatch.setattr(R, "stage2_annotate_pool_async", fake_annotate)
    monkeypatch.setattr(R, "load_courses_meta", lambda: {})
    monkeypatch.setattr(R, "load_careers", lambda: {})
    R.build_recommendation_instrumented(None, "store", "清潔工", seed=0, skills=["公共衛生"])
    assert captured["skills"] == ["公共衛生"]
