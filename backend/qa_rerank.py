"""問答檢索候選課程 LLM listwise rerank。

給定使用者意圖（問句）與候選課程池，用 LLM 依相關性重新排序並剔除不相關課程，
取前 top_k 筆。fail-open：任何例外（LLM 呼叫失敗、解析失敗等）一律回退為
「維持原順序、截前 top_k」，不讓 rerank 失敗拖垮整個問答流程。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class RerankOut(BaseModel):
    ranked_course_ids: list[str]


_RERANK_SYS = (
    "你是課程相關性排序器。給定使用者真實需求與候選課程，只保留真正符合需求的課、"
    "依相關性由高到低回傳其 course_id 順序（不相關的剔除）。嚴格依課程內容判斷，"
    "勿臆造 course_id。"
)


async def _call_rerank_llm(question: str, candidates: list) -> Optional[RerankOut]:
    from backend.openai_client import get_client
    from backend.recommend import OPENAI_MODEL

    client = get_client()
    if client is None:
        return None

    lines = [
        f'- {c["course_id"]}｜{c.get("name", "")}｜{(c.get("text", "") or "")[:180]}'
        for c in candidates
    ]
    resp = await client.responses.parse(
        model=OPENAI_MODEL,
        input=[
            {"role": "system", "content": _RERANK_SYS},
            {"role": "user", "content": f"需求：{question}\n候選：\n" + "\n".join(lines)},
        ],
        text_format=RerankOut,
    )
    return resp.output_parsed


async def rerank_courses(question: str, candidates: list, top_k: int = 10) -> list:
    """LLM listwise rerank 候選課程，fail-open 回退原順序前 top_k。"""
    if not candidates:
        return []
    try:
        parsed = await _call_rerank_llm(question, candidates)
        order = parsed.ranked_course_ids if parsed else []
        by_id = {c["course_id"]: c for c in candidates}
        ranked = [by_id[i] for i in order if i in by_id]
        return (ranked or candidates)[:top_k]
    except Exception:
        return candidates[:top_k]
