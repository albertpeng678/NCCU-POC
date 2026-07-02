# backend/dept_vocab.py
from __future__ import annotations
import json, re
from functools import lru_cache
from pathlib import Path

_VOCAB_PATH = Path(__file__).parent / "dept_canonical.json"

@lru_cache(maxsize=1)
def load_vocab() -> dict:
    return json.loads(_VOCAB_PATH.read_text(encoding="utf-8"))

_V = load_vocab()
CANONICAL_DEPTS: set[str] = {d for depts in _V["colleges"].values() for d in depts} | set(_V["special_units"])
DEPT_TO_COLLEGE: dict[str, str] = {d: c for c, depts in _V["colleges"].items() for d in depts}
DEPT_ALIASES: dict[str, str] = _V["dept_aliases"]
COLLEGE_ALIASES: dict[str, str] = _V["college_aliases"]
DEGREE_ALIASES: dict[str, str] = _V["degree_aliases"]
GE_PREFIXES: set[str] = set(_V["ge_course_id_prefixes"])

_STEM_RE = re.compile(r'^([一-鿿]+?)(?=[一二三四五六七八九十甲乙丙丁碩博]|\d)')

def strip_grade_tokens(s: str) -> str:
    """取系名詞幹：砍掉第一個年級/班別/碩博/數字起的尾段；無這些 token 則原樣返回
    （故「歷史系」保留、靠 alias 對映，「歷史一」→「歷史」、「歷史碩一歷史博一」→「歷史」）。"""
    m = _STEM_RE.match(s)
    return m.group(1) if m else s

def infer_degree_level(dirty: str, course_id: str) -> str:
    if course_id[:3] in GE_PREFIXES:
        return "通識"
    has_m, has_d = "碩" in dirty, "博" in dirty
    if has_m and has_d:
        return "碩博"
    if has_m:
        return "碩士"
    if has_d:
        return "博士"
    if re.search(r'[一二三四]', dirty) or dirty.endswith("系"):
        return "學士"
    return "其他"
