def test_build_uses_injected_skills(monkeypatch):
    import asyncio
    import backend.recommend as R
    captured = {}

    async def fake_fanout(client, store, career, skills):
        captured["skills"] = skills
        return [{"course_id": "000211012", "course_name": "X", "relevance": "r"}]

    async def fake_annotate(client, career, skills, candidates):
        from backend.recommend import _RankedOutput
        return _RankedOutput(courses=[])

    monkeypatch.setattr(R, "fanout_retrieve_async", fake_fanout)
    monkeypatch.setattr(R, "stage2_annotate_pool_async", fake_annotate)
    monkeypatch.setattr(R, "load_courses_meta", lambda: {})
    monkeypatch.setattr(R, "load_careers", lambda: {})
    R.build_recommendation_instrumented(None, "store", "清潔工", seed=0, skills=["公共衛生"])
    assert captured["skills"] == ["公共衛生"]
