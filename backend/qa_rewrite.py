"""問答檢索改造：把口語課程需求改寫成聚焦檢索 query + 抽可選系所/學制。

見 CLAUDE.md 設計決策 #26（condense-then-search，vendor-neutral）與
memory `qa-condense-plus-controlled-retrieval`：AI 負責把可能雜亂的口語需求
（職涯「想當PM」、系所縮寫「傳碩」、模糊需求）收斂成乾淨、聚焦的檢索意圖；
後端再用 `backend.dept_query` 的 normalize_* 把抽出的系所/學院/學制做受控篩選。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from backend.dept_query import normalize_college, normalize_degree, normalize_department

# college/degree_level 收斂成 Literal enum（而非自由文字）：gpt-5.4-mini 是 reasoning 模型，
# temperature/seed 不可控，複合縮寫（如「傳碩」）在自由文字欄位上每次拆法會漂移
# （college 有時填、有時不填；degree_level 同）。schema 層面強制值域收斂輸出、降低漂移。
# 12 學院值取自 backend.dept_vocab.load_vocab()["colleges"].keys()（見該檔常數 _COLLEGES 衍生處）。
_College = Literal[
    "文學院", "理學院", "社會科學學院", "法學院", "商學院", "外國語文學院",
    "傳播學院", "國際事務學院", "教育學院", "資訊學院", "創新國際學院", "國際金融學院",
]
_DegreeLevel = Literal["學士", "碩士", "博士", "研究所", "通識"]


class QueriesOut(BaseModel):
    queries: list[str]
    department: Optional[str] = None
    college: Optional[_College] = None
    degree_level: Optional[_DegreeLevel] = None


def _clamp_queries(qs: list[str]) -> list[str]:
    return [q.strip() for q in qs if q and q.strip()][:5]


_REWRITE_SYS = (
    "你是政大課程檢索助手。把使用者的口語課程需求，改寫成最多 5 個『聚焦、彼此互補、"
    "用政大課綱可能出現的正式詞彙』的檢索查詢字串（繁中）。"
    "①職涯/目標（如『想當PM』）→展開能力面向（產品管理、使用者體驗、數據分析、專案管理…）；"
    "②系所口語縮寫（『傳碩』=傳播學院碩士、『資管』=資訊管理）→展開正式名＋代表主題；"
    "③用課綱語彙（『使用者體驗』非『用戶研究』）。"
    "若明確指定系所/學院/學制，抽原文放對應欄位（沒有填 null）。"
    "重要：系所口語縮寫若同時隱含『系所或學院』與『學制』兩種資訊，兩個欄位都要填，"
    "不可只填其中一個。queries 至少 1、最多 5。\n"
    "範例：\n"
    "「推薦我傳碩十堂課」→ college=傳播學院, degree_level=碩士, department=null\n"
    "「資管碩士的課」→ department=資訊管理學系, degree_level=碩士, college=null\n"
    "「歷史系有什麼課」→ department=歷史學系, degree_level=null, college=null"
)


async def _call_rewrite_llm(question: str) -> Optional[QueriesOut]:
    from backend.openai_client import get_client
    from backend.recommend import OPENAI_MODEL

    client = get_client()
    if client is None:
        return None

    resp = await client.responses.parse(
        model=OPENAI_MODEL,
        input=[
            {"role": "system", "content": _REWRITE_SYS},
            {"role": "user", "content": question},
        ],
        text_format=QueriesOut,
        # extraction 任務不需深度推理；reasoning 越高，複合縮寫（「傳碩」）拆法漂移越明顯
        # （見 .superpowers/sdd/qa-retr-slotfix-report.md 實測）。gpt-5.4-mini 不支援
        # effort="minimal"（會 400 unsupported_value；實測支援值為 none/low/medium/high/xhigh），
        # 故用 "none"（最低、關閉 reasoning，等同 extraction 任務的 minimal 意圖）。
        reasoning={"effort": "none"},
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
