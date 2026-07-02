# backend/dept_query.py
from __future__ import annotations
from rapidfuzz import process, fuzz
from backend.dept_vocab import (
    CANONICAL_DEPTS, DEPT_ALIASES, COLLEGE_ALIASES, DEGREE_ALIASES,
    strip_grade_tokens,
)

_COLLEGES = set(COLLEGE_ALIASES.values()) | {"文學院","理學院","社會科學學院","法學院","商學院","外國語文學院","傳播學院","國際事務學院","教育學院","資訊學院","創新國際學院"}
_FUZZ_CUTOFF = 82  # 字面相似度門檻；低於此視為對不上

def _match(raw: str | None, alias: dict[str, str], canonical: set[str]) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    if raw in canonical:            # 層1a：已是標準值
        return raw
    if raw in alias:                # 層1b：別名快路
        return alias[raw]
    stem = strip_grade_tokens(raw)
    if stem in alias:
        return alias[stem]
    if len(raw) < 2:                # 短字串守門：太短模糊比對極易誤配，直接不做
        return None
    # 層2：rapidfuzz 最近鄰（治錯字），比對 canonical + alias keys
    # 用 fuzz.ratio（長度敏感、不做 partial 子字串灌水）而非 WRatio，避免短字串/亂打誤配
    pool = list(canonical) + list(alias.keys())
    hit = process.extractOne(raw, pool, scorer=fuzz.ratio, score_cutoff=_FUZZ_CUTOFF)
    if hit:
        val = hit[0]
        return alias.get(val, val)
    return None                     # 層3：對不上 → None（不過濾）

def normalize_department(raw: str | None) -> str | None:
    return _match(raw, DEPT_ALIASES, CANONICAL_DEPTS)

def normalize_college(raw: str | None) -> str | None:
    return _match(raw, COLLEGE_ALIASES, _COLLEGES)

def normalize_degree(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    if raw in DEGREE_ALIASES:
        return DEGREE_ALIASES[raw]
    if len(raw) < 2:                # 短字串守門：太短模糊比對極易誤配，直接不做
        return None
    hit = process.extractOne(raw, list(DEGREE_ALIASES.keys()), scorer=fuzz.ratio, score_cutoff=_FUZZ_CUTOFF)
    return DEGREE_ALIASES[hit[0]] if hit else None
