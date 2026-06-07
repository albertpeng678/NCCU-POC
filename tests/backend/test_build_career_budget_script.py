# tests/backend/test_build_career_budget_script.py
"""離線預算腳本的可測核心：select_careers_to_build（決定要算哪些職涯）。"""
import importlib.util
import os

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                     "scripts", "build_career_budget.py")
_spec = importlib.util.spec_from_file_location("build_career_budget", _PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
select_careers_to_build = _mod.select_careers_to_build

_ALL = ["產品經理(PM)", "資料科學家", "軟體工程師"]


def test_only_returns_single_if_valid():
    assert select_careers_to_build(_ALL, set(), only="資料科學家") == ["資料科學家"]


def test_only_invalid_returns_empty():
    assert select_careers_to_build(_ALL, set(), only="不存在") == []


def test_default_returns_all():
    assert select_careers_to_build(_ALL, set()) == _ALL


def test_only_missing_excludes_existing():
    assert select_careers_to_build(_ALL, {"資料科學家"}, only_missing=True) == ["產品經理(PM)", "軟體工程師"]


def test_only_missing_all_done_returns_empty():
    assert select_careers_to_build(_ALL, set(_ALL), only_missing=True) == []
