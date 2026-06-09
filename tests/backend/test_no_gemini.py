# tests/backend/test_no_gemini.py
"""
回歸守門：部署的 backend runtime 必須與 Gemini「毫無關係」。

斷言：
1. backend 的 runtime 模組原始碼不再 import google / google.genai。
2. backend.main 模組載入後沒有 Gemini client 屬性（_client / _GEMINI_API_KEY 等）。
3. backend/requirements.txt 不再依賴 google-genai。

ingestion/ 是一次性本地建庫工具、不在部署路徑，故不在此守門範圍。
"""
from __future__ import annotations
import re
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
_RUNTIME_MODULES = ["main.py", "qa.py", "recommend.py", "observability.py",
                    "judge.py", "qa_judge.py", "models.py", "logger.py",
                    "qa_logger.py", "career_budget.py", "openai_client.py",
                    "retrieval_openai.py", "db.py"]


@pytest.mark.parametrize("modname", _RUNTIME_MODULES)
def test_no_google_genai_import(modname):
    src = (_BACKEND / modname).read_text(encoding="utf-8")
    # 抓任何 `import google` / `from google...` / `genai` 參照（排除註解中的字）
    offenders = []
    for i, line in enumerate(src.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if re.search(r"\b(from google\b|import google\b|google\.genai|genai\.)", stripped):
            offenders.append(f"{modname}:{i}: {stripped}")
    assert not offenders, "backend runtime 仍 import Gemini:\n" + "\n".join(offenders)


def test_main_has_no_gemini_client_attrs():
    import backend.main as m
    for attr in ("_client", "_GEMINI_API_KEY", "_STORE_NAME", "_QA_MODEL"):
        assert not hasattr(m, attr), f"backend.main 仍有 Gemini 屬性 {attr}"


def test_requirements_drops_google_genai():
    req = (_BACKEND / "requirements.txt").read_text(encoding="utf-8")
    assert "google-genai" not in req, "backend/requirements.txt 仍依賴 google-genai"
