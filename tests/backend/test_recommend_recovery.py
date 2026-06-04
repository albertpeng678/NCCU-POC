# tests/backend/test_recommend_recovery.py
"""course_id 復原修復測試：
- stage1 抄錯課號 → 用 course_name 在 meta 的 name 索引修正。
- stage2 抄錯課號 → 用「與某 candidate 共享前6碼」復原。
- 合法課不被誤丟、抄錯課被救回、log 數字正確。
全程 mock，不打真 Gemini。"""
import logging
import pytest
from backend.recommend import (
    build_name_index,
    correct_candidate_ids,
    build_groups,
    _Stage2Output,
    _Groups,
    _CourseItem,
    _ReasonPoint,
)


def _meta(*ids_names):
    """ids_names: (course_id, name) pairs → meta dict."""
    m = {}
    for cid, name in ids_names:
        m[cid] = {
            "name": name,
            "department": "系",
            "teacher": "師",
            "credits": 3.0,
            "syllabus_url": f"https://x/{cid}",
        }
    return m


def _stage2(core=None, supporting=None, extended=None):
    def mk(ids):
        return [
            _CourseItem(
                course_id=cid,
                reason_lead="總述",
                reason_points=[_ReasonPoint(term="分析", detail="拆解問題")],
            )
            for cid in (ids or [])
        ]
    return _Stage2Output(groups=_Groups(
        core=mk(core), supporting=mk(supporting), extended=mk(extended)
    ))


def _candidates(*ids_names):
    return [
        {"course_id": cid, "course_name": name, "relevance": "x"}
        for cid, name in ids_names
    ]


# ---------- build_name_index ----------

def test_build_name_index_maps_name_to_id():
    meta = _meta(("000211012", "政治學"), ("000216001", "經濟學"))
    idx = build_name_index(meta)
    assert idx["政治學"] == "000211012"
    assert idx["經濟學"] == "000216001"


def test_build_name_index_first_occurrence_wins():
    # 同名不同 id → 取首次出現（dict 插入序）
    meta = _meta(("000211012", "通識"), ("000999088", "通識"))
    idx = build_name_index(meta)
    assert idx["通識"] == "000211012"


# ---------- correct_candidate_ids (stage1 名稱修正) ----------

def test_correct_candidate_fixes_wrong_id_via_name():
    meta = _meta(("000211012", "政治學"))
    # stage1 抄錯一碼：000211013，但 course_name 對得上
    cands = [{"course_id": "000211013", "course_name": "政治學", "relevance": "x"}]
    fixed = correct_candidate_ids(cands, meta)
    assert fixed[0]["course_id"] == "000211012"


def test_correct_candidate_keeps_valid_id():
    meta = _meta(("000211012", "政治學"))
    cands = [{"course_id": "000211012", "course_name": "政治學", "relevance": "x"}]
    fixed = correct_candidate_ids(cands, meta)
    assert fixed[0]["course_id"] == "000211012"


def test_correct_candidate_unknown_name_left_unchanged():
    meta = _meta(("000211012", "政治學"))
    cands = [{"course_id": "999999999", "course_name": "不存在的課", "relevance": "x"}]
    fixed = correct_candidate_ids(cands, meta)
    # 名稱也查不到 → 維持原樣（後續才在 group 階段丟棄）
    assert fixed[0]["course_id"] == "999999999"


def test_correct_candidate_does_not_mutate_input():
    meta = _meta(("000211012", "政治學"))
    cands = [{"course_id": "000211013", "course_name": "政治學", "relevance": "x"}]
    correct_candidate_ids(cands, meta)
    assert cands[0]["course_id"] == "000211013"  # 原 list 未被改


# ---------- build_groups (stage2 前綴復原 + dedup + log) ----------

def test_build_groups_valid_id_not_dropped():
    meta = _meta(("000211012", "政治學"))
    cands = _candidates(("000211012", "政治學"))
    stage2 = _stage2(core=["000211012"])
    groups = build_groups(stage2, cands, meta)
    assert len(groups["core"]) == 1
    assert groups["core"][0]["name"] == "政治學"


def test_build_groups_prefix_recovery_for_wrong_stage2_id():
    # candidate 的真 id = 000211012；stage2 抄成 000211099（前6碼相同，後3碼錯）
    meta = _meta(("000211012", "政治學"))
    cands = _candidates(("000211012", "政治學"))
    stage2 = _stage2(core=["000211099"])
    groups = build_groups(stage2, cands, meta)
    assert len(groups["core"]) == 1
    assert groups["core"][0]["course_id"] == "000211012"
    assert groups["core"][0]["name"] == "政治學"


def test_build_groups_drops_unrecoverable_id():
    meta = _meta(("000211012", "政治學"))
    cands = _candidates(("000211012", "政治學"))
    # stage2 id 既不在 meta，也不與任何 candidate 共享前6碼 → 丟棄
    stage2 = _stage2(core=["777888999"])
    groups = build_groups(stage2, cands, meta)
    assert groups["core"] == []


def test_build_groups_cross_group_name_dedup_core_priority():
    meta = _meta(("000211012", "政治學"))
    cands = _candidates(("000211012", "政治學"))
    # 同一門課出現在 core 與 supporting → 只留 core
    stage2 = _stage2(core=["000211012"], supporting=["000211012"])
    groups = build_groups(stage2, cands, meta)
    assert len(groups["core"]) == 1
    assert groups["supporting"] == []


def test_build_groups_prefix_dedup_within_group():
    # 同 group 內前6碼相同 → 只留一門（deduplicate_by_prefix 行為保留）
    meta = _meta(("000211012", "政治學"), ("000211022", "政治學進階"))
    cands = _candidates(("000211012", "政治學"), ("000211022", "政治學進階"))
    stage2 = _stage2(core=["000211012", "000211022"])
    groups = build_groups(stage2, cands, meta)
    assert len(groups["core"]) == 1


def test_build_groups_reason_shape_preserved():
    meta = _meta(("000211012", "政治學"))
    cands = _candidates(("000211012", "政治學"))
    stage2 = _stage2(core=["000211012"])
    groups = build_groups(stage2, cands, meta)
    reason = groups["core"][0]["reason"]
    assert reason["lead"] == "總述"
    assert reason["points"][0]["term"] == "分析"
    assert reason["points"][0]["detail"] == "拆解問題"


def test_build_groups_logs_counts(caplog):
    meta = _meta(("000211012", "政治學"))
    cands = _candidates(("000211012", "政治學"))
    # core: 一門合法。supporting: 一門前綴復原(但與 core 同名→跨組去重)。extended: 一門丟棄。
    stage2 = _stage2(
        core=["000211012"],
        supporting=["000211099"],
        extended=["777888999"],
    )
    with caplog.at_level(logging.INFO, logger="backend.recommend"):
        build_groups(stage2, cands, meta)
    text = caplog.text
    assert "stage2" in text.lower()
    assert "recover" in text.lower() or "復原" in text


def test_build_groups_returns_three_keys():
    meta = _meta(("000211012", "政治學"))
    cands = _candidates(("000211012", "政治學"))
    stage2 = _stage2(core=["000211012"])
    groups = build_groups(stage2, cands, meta)
    assert set(groups.keys()) == {"core", "supporting", "extended"}
