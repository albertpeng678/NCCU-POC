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
    import backend.recommend as R
    captured = {}
    def fake_stage1(client, store, career, skills):
        captured["skills"] = skills
        return [{"course_id":"000211012","course_name":"X","relevance":"r"}]
    def fake_stage2(client, career, skills, candidates):
        from backend.recommend import _Stage2Output, _Groups
        return _Stage2Output(groups=_Groups(core=[],supporting=[],extended=[]))
    monkeypatch.setattr(R,"stage1_retrieve",fake_stage1)
    monkeypatch.setattr(R,"stage2_group",fake_stage2)
    monkeypatch.setattr(R,"load_courses_meta",lambda:{})
    monkeypatch.setattr(R,"load_careers",lambda:{})
    R.build_recommendation_instrumented(None,"store","清潔工",seed=0,skills=["公共衛生"])
    assert captured["skills"] == ["公共衛生"]
