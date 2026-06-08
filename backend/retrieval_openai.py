"""
OpenAI Vector Store 檢索層。

兩個函式：
- search_skill: 對 vector store 搜一個技能查詢，正規化回 list[dict]。
- course_ids_from_annotations: 從 Responses API annotations 萃取 course_id 清單。
"""

from __future__ import annotations

from typing import Any


async def search_skill(
    client: Any,
    vs_id: str,
    query: str,
    top_k: int = 8,
    score_threshold: float = 0.0,
) -> list[dict]:
    """Search a single skill query against the vector store.

    Returns a list of dicts with keys: course_id, score, content.
    Results missing course_id in attributes are skipped.
    """
    resp = await client.vector_stores.search(
        vector_store_id=vs_id,
        query=query,
        max_num_results=top_k,
        ranking_options={"score_threshold": score_threshold},
    )

    results = []
    for item in resp.data:
        course_id = (item.attributes or {}).get("course_id")
        if not course_id:
            continue
        content = "".join(c.text for c in item.content)
        results.append({"course_id": course_id, "score": item.score, "content": content})
    return results


def course_ids_from_annotations(annotations: list[Any]) -> list[str]:
    """Extract course_ids from Responses API annotations.

    Only considers type=="file_citation" entries.
    Strips the ".txt" extension from filename to get course_id.
    Deduplicates while preserving order.
    """
    seen: set[str] = set()
    result: list[str] = []
    for ann in annotations:
        if getattr(ann, "type", None) != "file_citation":
            continue
        filename = getattr(ann, "filename", None)
        if not filename:
            continue
        course_id = filename.removesuffix(".txt")
        if course_id not in seen:
            seen.add(course_id)
            result.append(course_id)
    return result
