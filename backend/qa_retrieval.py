"""問答檢索改造：門面（Step A）。

串起三個 leaf 模組——rewrite_to_queries（口語→聚焦 query + 輔助 slots）、
multi_query_search（多路 query 一次檢索 + 系所輔助 filter）、rerank_courses
（LLM listwise rerank 剔除不相關）——組成單一入口 retrieve_and_rerank(question, vs_id)。

任一環節空（rewrite 沒查到、search 空、rerank 空）→ 回 (None, [])，呼叫端
（backend.qa.stream_answer）據此 fail-open 落回既有 file_search 路徑。
"""

from __future__ import annotations

from backend.qa_rewrite import rewrite_to_queries
from backend.qa_multiquery import multi_query_search
from backend.qa_rerank import rerank_courses
from backend.recommend import load_courses_meta


def _norm_cands(data: list) -> list:
    """把 multi_query_search 回傳的原始搜尋結果（SDK 物件或 dict）正規化成統一 dict 形狀。

    name 欄位查 courses_meta.json 拿真課名（key=course_id）；filename 是「課號.txt」，
    不是課名，查無 meta 時才 fallback 回 filename 去掉 .txt（不崩、但保留舊行為）。
    """
    meta = load_courses_meta()
    out = []
    for d in data:
        a = (getattr(d, "attributes", None) if not isinstance(d, dict) else d.get("attributes")) or {}
        fn = (getattr(d, "filename", "") if not isinstance(d, dict) else d.get("filename", "")) or ""
        content = (getattr(d, "content", None) if not isinstance(d, dict) else d.get("content")) or []
        cid = a.get("course_id") or fn.replace(".txt", "")
        text = "".join(getattr(x, "text", "") if not isinstance(x, dict) else x.get("text", "") for x in content)
        name = (meta.get(cid) or {}).get("name") or fn.replace(".txt", "")
        out.append({"course_id": cid, "name": name, "filename": fn, "attributes": a, "content": content, "text": text})
    return out


def _fmt(cands: list) -> str:
    """把去重後的候選課程組成注入 prompt 的 context 文字。"""
    rows = []
    for c in cands:
        a = c.get("attributes") or {}
        rows.append(f'課名：{c.get("name", "")}｜系所：{a.get("dept_canonical", "")}\n{(c.get("text", "") or "")[:1200]}')
    return "\n\n".join(rows)


async def retrieve_and_rerank(question: str, vs_id: str) -> tuple[str | None, list[str]]:
    """rewrite→多路檢索→rerank 一條龍，回 (context_text, course_ids)；任一環節空回 (None, [])。"""
    slots = await rewrite_to_queries(question)
    data = await multi_query_search(slots["queries"], slots, vs_id)
    if not data:
        return None, []
    ranked = await rerank_courses(question, _norm_cands(data), top_k=10)
    if not ranked:
        return None, []
    seen: set[str] = set()
    uniq = []
    for c in ranked:
        k = (c.get("course_id") or "")[:6]
        if k and k in seen:
            continue
        seen.add(k)
        uniq.append(c)
    return _fmt(uniq), [c["course_id"] for c in uniq if c.get("course_id")]
