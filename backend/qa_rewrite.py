"""問答檢索改造：把口語課程需求改寫成聚焦檢索 query + 抽可選系所/學制。

見 CLAUDE.md 設計決策 #26（condense-then-search，vendor-neutral）與
memory `qa-condense-plus-controlled-retrieval`：AI 負責把可能雜亂的口語需求
（職涯「想當PM」、系所縮寫「傳碩」、模糊需求）收斂成乾淨、聚焦的檢索意圖；
後端再用 `backend.dept_query` 的 normalize_* 把抽出的系所/學院/學制做受控篩選。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from backend.dept_query import normalize_college, normalize_degree, normalize_department


class QueriesOut(BaseModel):
    queries: list[str]
    department: Optional[str] = None
    college: Optional[str] = None
    degree_level: Optional[str] = None


def _clamp_queries(qs: list[str]) -> list[str]:
    return [q.strip() for q in qs if q and q.strip()][:5]


_REWRITE_SYS = (
    "你是政大課程檢索助手。把使用者的口語課程需求，改寫成最多 5 個『聚焦、彼此互補、"
    "用政大課綱可能出現的正式詞彙』的檢索查詢字串（繁中）。"
    "①職涯/目標（如『想當PM』）→展開能力面向（產品管理、使用者體驗、數據分析、專案管理…）；"
    "②系所口語縮寫（『傳碩』=傳播學院碩士、『資管』=資訊管理）→展開正式名＋代表主題；"
    "③用課綱語彙（『使用者體驗』非『用戶研究』）。"
    "若明確指定系所/學院/學制，抽原文放對應欄位（沒有填 null）。queries 至少 1、最多 5。"
)


async def _call_rewrite_llm(question: str) -> QueriesOut:
    from backend.openai_client import get_client
    from backend.recommend import OPENAI_MODEL

    resp = await get_client().responses.parse(
        model=OPENAI_MODEL,
        input=[
            {"role": "system", "content": _REWRITE_SYS},
            {"role": "user", "content": question},
        ],
        text_format=QueriesOut,
    )
    return resp.output_parsed


async def rewrite_to_queries(question: str) -> dict:
    try:
        parsed = await _call_rewrite_llm(question)
        qs = _clamp_queries(parsed.queries) if parsed else []
    except Exception:
        parsed = None
        qs = []

    if not qs:
        return {"queries": [question], "department": None, "college": None, "degree_level": None}

    return {
        "queries": qs,
        "department": normalize_department(getattr(parsed, "department", None)),
        "college": normalize_college(getattr(parsed, "college", None)),
        "degree_level": normalize_degree(getattr(parsed, "degree_level", None)),
    }
