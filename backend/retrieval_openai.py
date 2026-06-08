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
    results = []
    async for item in client.vector_stores.search(
        vector_store_id=vs_id,
        query=query,
        max_num_results=top_k,
        ranking_options={"score_threshold": score_threshold},
    ):
        course_id = (item.attributes or {}).get("course_id")
        if not course_id:
            continue
        content = "".join(c.text for c in (item.content or []))
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


def course_ids_from_search_results(results: list[Any]) -> list[str]:
    """Extract course_ids from file_search_call.results (Responses API with include=[...]).

    Each result may have:
    - .attributes (dict) with key "course_id"  ← canonical, used when available
    - .filename like "000211012.txt"             ← fallback, strip ".txt"

    Deduplicates while preserving order (by score, highest first as returned by API).
    """
    seen: set[str] = set()
    result_ids: list[str] = []
    for r in results or []:
        # Try canonical attributes.course_id first
        attrs = getattr(r, "attributes", None)
        if isinstance(attrs, dict):
            cid = attrs.get("course_id")
            if cid and cid not in seen:
                seen.add(cid)
                result_ids.append(cid)
                continue
        # Fallback: strip ".txt" from filename
        filename = getattr(r, "filename", None)
        if filename:
            cid = filename.removesuffix(".txt")
            if cid and cid not in seen:
                seen.add(cid)
                result_ids.append(cid)
    return result_ids
