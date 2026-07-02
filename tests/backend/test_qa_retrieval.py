import asyncio
import backend.qa_retrieval as qr


def test_norm_cands_uses_real_course_name_from_meta(monkeypatch):
    """_norm_cands 的 name 欄位必須是 courses_meta.json 查到的真課名，不是 filename（課號.txt）。

    見 CLAUDE.md review finding：fn 是 filename=「課號.txt」，name==course_id 是誤標，
    餵給 rerank LLM 訊號弱、_fmt 顯示也會誤標成「課名：702888001.txt」。
    """
    meta = {"000211012": {"course_id": "000211012", "name": "政治學"}}
    monkeypatch.setattr(qr, "load_courses_meta", lambda: meta)
    data = [{"course_id": None, "filename": "000211012.txt", "content": [{"text": "hi"}], "attributes": {}}]
    out = qr._norm_cands(data)
    assert out[0]["name"] == "政治學"
    assert out[0]["name"] != "000211012.txt".replace(".txt", "")


def test_norm_cands_falls_back_to_filename_when_meta_missing(monkeypatch):
    """courses_meta 查無此 course_id 時，維持舊行為 fallback 回 filename 去掉 .txt（不崩）。"""
    monkeypatch.setattr(qr, "load_courses_meta", lambda: {})
    data = [{"course_id": None, "filename": "999999999.txt", "content": [{"text": "hi"}], "attributes": {}}]
    out = qr._norm_cands(data)
    assert out[0]["name"] == "999999999"


def test_fmt_uses_real_course_name(monkeypatch):
    """_fmt 組出的 context 文字要用真課名，不是 filename。"""
    cands = [{"course_id": "000211012", "name": "政治學", "filename": "000211012.txt",
              "attributes": {"dept_canonical": "政治學系"}, "text": "課程內容"}]
    text = qr._fmt(cands)
    assert "課名：政治學｜系所：政治學系" in text
    assert "000211012.txt" not in text


def test_happy(monkeypatch):
    async def fr(q): return {"queries": ["x"], "department": None, "college": None, "degree_level": None}
    async def fs(qs, slots, vs, **k): return [{"course_id": "A", "filename": "A.txt", "content": [{"text": "hi"}], "attributes": {"course_id": "A", "dept_canonical": "歷史學系"}}]
    async def fk(q, c, top_k=10): return c
    monkeypatch.setattr(qr, "rewrite_to_queries", fr)
    monkeypatch.setattr(qr, "multi_query_search", fs)
    monkeypatch.setattr(qr, "rerank_courses", fk)
    ctx, ids = asyncio.run(qr.retrieve_and_rerank("q", "vs"))
    assert ctx and ids == ["A"]


def test_empty_search(monkeypatch):
    async def fr(q): return {"queries": ["x"], "department": None, "college": None, "degree_level": None}
    async def fs(*a, **k): return []
    monkeypatch.setattr(qr, "rewrite_to_queries", fr)
    monkeypatch.setattr(qr, "multi_query_search", fs)
    assert asyncio.run(qr.retrieve_and_rerank("q", "vs")) == (None, [])
