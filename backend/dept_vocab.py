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
GE_PREFIXES: set[str] = set(_V["ge_course_id_prefixes"])

# canonical degree_level 值域固定為這 6 個（見 build_dept_filter / normalize_degree 的下游用法）。
CANONICAL_DEGREES: set[str] = {"學士", "碩士", "博士", "碩博", "通識", "其他"}

# Critical bug fix（review 實測）：DEGREE_ALIASES 原始資料（dept_canonical.json 的
# degree_aliases）沒有幫每個 canonical degree_level 值補自我對映鍵（例如沒有 "通識":"通識"、
# "學士":"學士"、"碩博":"碩博"），導致 normalize_degree() 對「已是 canonical 值」的原文
# 反而找不到直接命中，落到 rapidfuzz 模糊比對——"通識" 對現有 alias key 相似度太低直接 miss，
# "學士" 對 alias key "學士班" 的 fuzz.ratio(80) < cutoff(82) 也 miss。
# 兩者都回 None，會讓 build_dept_filter 誤判成「使用者未指定學制」，走到「系所/學院有值但學制
# 沒指定 → 預設加 ne 通識排除通識課」的分支——「歷史系的通識課」因此被反著處理成排除通識。
# 修法：在這裡（衍生層，不動來源 JSON）合併補上 6 個 canonical 值的自我對映鍵，讓
# normalize_degree() 的直接 key 命中（層1）就能覆蓋所有 canonical 值，不必依賴模糊比對。
# 只在 dept_query.py 使用（grep 確認），不影響 infer_degree_level（規則式、不讀 DEGREE_ALIASES）
# 或 ingestion 建庫端（同樣不讀這張表）。
DEGREE_ALIASES: dict[str, str] = {
    **_V["degree_aliases"],
    **{d: d for d in CANONICAL_DEGREES},
}

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
