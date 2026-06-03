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
