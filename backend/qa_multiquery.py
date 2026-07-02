"""問答檢索改造：multi_query_search。

把 ≤5 個 query 一次傳給 OpenAI `vector_stores.search`（該 API 的 `query` 參數支援
陣列，且上限 5 個），並帶可選的系所輔助 filter（見 backend.dept_query.build_dept_filter）。
撈空則去掉 filter 重搜一次（fail-open：系所判斷錯誤/過嚴時不至於讓使用者拿到空結果）。

全 async、不引入 Gemini。
"""

from __future__ import annotations

from backend.dept_query import build_dept_filter


def _clamp(qs: list[str]) -> list[str]:
    """去除空白/空字串 query，並 clamp 到 OpenAI vector_stores.search 的上限 5 個。"""
    return [q.strip() for q in qs if q and q.strip()][:5]


async def _vs_search(**kwargs):
    """薄包裝 OpenAI `vector_stores.search`，方便測試時 monkeypatch。

    OPENAI_API_KEY 未設（get_client() 回 None）時回 None，讓呼叫端 fail-open 當空結果處理。
    """
    from backend.openai_client import get_client

    client = get_client()
    if client is None:
        return None

    return await client.vector_stores.search(**kwargs)


async def multi_query_search(
    queries: list[str],
    slots: dict,
    vs_id: str,
    max_num_results: int = 24,
) -> list:
    """用（可能多個）query 一次搜尋 vector store，帶系所輔助 filter；撈空則去 filter 重搜。

    Args:
        queries: 查詢字串清單（會 clamp 到 5 個、去除空白）。
        slots: extract_slots 正規化後的 {department, college, degree_level}（可為空 dict）。
        vs_id: OpenAI vector store id。
        max_num_results: 傳給 vector_stores.search 的 max_num_results。

    Returns:
        list：命中的搜尋結果（`response.data`）；queries clamp 後為空則直接回 []。
    """
    q = _clamp(queries)
    if not q:
        return []

    filt = build_dept_filter(
        {k: slots.get(k) for k in ("department", "college", "degree_level")}
    )

    async def _run(f):
        r = await _vs_search(
            vector_store_id=vs_id,
            query=q,
            max_num_results=max_num_results,
            filters=f,
        )
        return list(getattr(r, "data", []) or []) if r is not None else []

    data = await _run(filt)
    if not data and filt is not None:
        data = await _run(None)
    return data
