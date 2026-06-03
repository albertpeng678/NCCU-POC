# tests/backend/test_recommend_diversity.py
from backend.recommend import sample_candidates, ANCHOR_COUNT, SAMPLE_SIZE


def _pool(n):
    return [{"course_id": f"{i:09d}", "course_name": f"課{i}", "relevance": "x"} for i in range(n)]


def test_same_seed_is_reproducible():
    pool = _pool(40)
    a = sample_candidates(pool, seed=123)
    b = sample_candidates(pool, seed=123)
    assert [c["course_id"] for c in a] == [c["course_id"] for c in b]


def test_different_seed_changes_selection():
    pool = _pool(40)
    a = sample_candidates(pool, seed=1)
    b = sample_candidates(pool, seed=2)
    assert [c["course_id"] for c in a] != [c["course_id"] for c in b]


def test_anchors_always_included():
    pool = _pool(40)
    result_ids = {c["course_id"] for c in sample_candidates(pool, seed=7)}
    for i in range(ANCHOR_COUNT):
        assert f"{i:09d}" in result_ids


def test_result_size_and_no_duplicates():
    pool = _pool(40)
    result = sample_candidates(pool, seed=7)
    ids = [c["course_id"] for c in result]
    assert len(ids) == SAMPLE_SIZE
    assert len(set(ids)) == len(ids)


def test_small_pool_returned_as_is():
    pool = _pool(5)
    result = sample_candidates(pool, seed=7)
    assert [c["course_id"] for c in result] == [c["course_id"] for c in pool]
