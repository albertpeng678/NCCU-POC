import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from backend.retrieval_openai import search_skill, course_ids_from_annotations


def test_search_skill_normalizes_results():
    fake = SimpleNamespace(
        vector_stores=SimpleNamespace(
            search=AsyncMock(
                return_value=SimpleNamespace(
                    data=[
                        SimpleNamespace(
                            score=0.9,
                            attributes={"course_id": "070415001"},
                            content=[SimpleNamespace(text="資料科學課綱")],
                        )
                    ]
                )
            )
        )
    )
    out = asyncio.run(
        search_skill(fake, "vs_1", "資料分析", top_k=8, score_threshold=0.0)
    )
    assert out == [{"course_id": "070415001", "score": 0.9, "content": "資料科學課綱"}]


def test_course_ids_from_annotations_strips_ext():
    anns = [
        SimpleNamespace(type="file_citation", filename="070415001.txt", file_id="f1"),
        SimpleNamespace(type="file_citation", filename="070415002.txt", file_id="f2"),
    ]
    assert course_ids_from_annotations(anns) == ["070415001", "070415002"]


def test_course_ids_dedup_and_ignore_nonfile():
    anns = [
        SimpleNamespace(type="file_citation", filename="070415001.txt", file_id="f1"),
        SimpleNamespace(type="url_citation", filename=None, file_id=None),
        SimpleNamespace(type="file_citation", filename="070415001.txt", file_id="f1"),
    ]
    assert course_ids_from_annotations(anns) == ["070415001"]
