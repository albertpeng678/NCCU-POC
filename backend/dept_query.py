# backend/dept_query.py
from __future__ import annotations
from typing import Optional

from pydantic import BaseModel
from pypinyin import lazy_pinyin
from rapidfuzz import process, fuzz
from backend.dept_vocab import (
    CANONICAL_DEPTS, DEPT_ALIASES, COLLEGE_ALIASES, DEGREE_ALIASES, CANONICAL_DEGREES,
    strip_grade_tokens, load_vocab,
)

# Task 6 concern #2（false-negative 修復）：_COLLEGES 改從 vocab 衍生，不再硬編清單。
# 舊版硬編清單漏收錄「國際金融學院」（vocab 的 colleges 有這個 key，硬編列表沒有）——
# 從 load_vocab()["colleges"].keys() 衍生後，vocab 新增學院不會再需要同步修這裡。
_COLLEGES = set(load_vocab()["colleges"].keys()) | set(COLLEGE_ALIASES.values())
_FUZZ_CUTOFF = 82  # 字面相似度門檻；低於此視為對不上

# 通用學術單位字尾（依長度由長至短排列，供 _core() 逐一比對結尾——
# 必須先試長字尾（如「研究所」「學位學程」）再試短字尾（「所」「院」），
# 否則「研究所」會被短字尾「所」搶先只砍一個字，砍剩「XX研究」而非「XX」）。
_UNIT_SUFFIXES = (
    "學位學程", "在職專班",
    "研究所",
    "學系", "學院", "學程", "專班", "碩士", "博士", "中心",
    "系", "院", "所", "室",
)

def _core(s: str) -> str:
    """砍掉結尾的通用學術單位字尾，取有辨識度的核心名。
    例：立是系→立是、歷史學系→歷史、心裡系→心裡、心理學系→心理、數學系→數學、醫學院→醫。
    只砍一層（非迭代），且不砍到空字串（len(s) > len(suf) 才砍）。"""
    for suf in _UNIT_SUFFIXES:
        if s.endswith(suf) and len(s) > len(suf):
            return s[: -len(suf)]
    return s

def _pinyin(s: str) -> str:
    return "".join(lazy_pinyin(s))

def _pinyin_core(s: str) -> str:
    return _pinyin(_core(s))

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
        # Task 6 concern #1 守門：候選字串與 raw 互為子字串包含（如「護理學院」⊃「理學院」、
        # 「管理學院」⊃「理學院」、「人文學院」⊃「文學院」）時，fuzz.ratio 會被包含關係灌到
        # >= cutoff 而誤配——這是前後綴增減（不同系所/學院），不是錯字，拒絕接受此模糊命中。
        # 不影響「資訊管里學系→資訊管理學系」這種單字替換型錯字（兩者互不包含）。
        if val not in raw and raw not in val:
            return alias.get(val, val)
    # 層3：拼音比對（治同音字，如「立是系」拼音同「歷史系」但字形無關，rapidfuzz 字形比對抓不到）。
    # 池只用 canonical（不含 alias.keys()）：alias 多為短別名，轉拼音後長度被壓縮，
    # 會讓不相關字串（如「商院子」vs 別名「商院」）因長度正規化虛高命中而誤配；
    # 已知同音別名（如「歷史系」）本就會在層1b 精確比對命中，不依賴這層。
    # ⚠️ 比對前先用 _core() 砍掉「學系/學院」等通用單位字尾、只比核心拼音，且要求「精確相等」——
    # 否則通用字尾會把短查詢灌分（如「數學系」vs「社會學系」fuzz.ratio 84.21、
    # 「醫學院」vs「理學院」88.89），造成真系名被誤配到不相關系所/學院（Critical false-positive，
    # 見 .superpowers/sdd/task-6a-fix-report.md）。
    raw_core_py = _pinyin_core(raw)
    if raw_core_py:
        for cand in canonical:
            if _pinyin_core(cand) == raw_core_py:
                return alias.get(cand, cand)
    return None                     # 層4：對不上 → None（不過濾）

def normalize_department(raw: str | None) -> str | None:
    return _match(raw, DEPT_ALIASES, CANONICAL_DEPTS)

def normalize_college(raw: str | None) -> str | None:
    return _match(raw, COLLEGE_ALIASES, _COLLEGES)

def normalize_degree(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    if raw in CANONICAL_DEGREES:    # 層1a：已是 canonical 值，原樣返回（不依賴 DEGREE_ALIASES
        return raw                  # 是否有自我對映鍵——雙保險，見 dept_vocab.py 的補鍵註解）
    if raw in DEGREE_ALIASES:
        return DEGREE_ALIASES[raw]
    if len(raw) < 2:                # 短字串守門：太短模糊比對極易誤配，直接不做
        return None
    hit = process.extractOne(raw, list(DEGREE_ALIASES.keys()), scorer=fuzz.ratio, score_cutoff=_FUZZ_CUTOFF)
    return DEGREE_ALIASES[hit[0]] if hit else None


# ─────────────────────────── build_dept_filter ───────────────────────────

# degree_level 值展開成 OpenAI filter 要 IN 的值集合（碩博合開課要同時涵蓋）。
# 「學士」「通識」不在此表 → 走精確 eq（見 build_dept_filter）。
_DEGREE_IN: dict[str, list[str]] = {
    "碩士": ["碩士", "碩博"],
    "博士": ["博士", "碩博"],
    "研究所": ["碩士", "博士", "碩博"],
}


def build_dept_filter(slots: dict) -> dict | None:
    """把 extract_slots 正規化後的 {department, college, degree_level} 組成 OpenAI vector store filter。

    決定#2（通識排除）：使用者指定系所/學院、但沒指定學制時，預設排除通識課
    （額外加一條 {"type":"ne","key":"degree_level","value":"通識"}）——避免「歷史系的課」
    這種查詢混進全校共通的通識課。若使用者已明確指定學制（含明確要通識），尊重原意、不加此條。

    空 slots → None；單一 clause → 直接回該 clause；多條 → {"type":"and","filters":[...]}。
    """
    clauses: list[dict] = []
    department = slots.get("department")
    college = slots.get("college")
    if department:
        clauses.append({"type": "eq", "key": "dept_canonical", "value": department})
    if college:
        clauses.append({"type": "eq", "key": "college", "value": college})

    degree_level = slots.get("degree_level")
    if degree_level:
        if degree_level in _DEGREE_IN:
            clauses.append({"type": "in", "key": "degree_level", "value": _DEGREE_IN[degree_level]})
        else:  # 學士/通識 等精確值
            clauses.append({"type": "eq", "key": "degree_level", "value": degree_level})
    elif department or college:
        # 決定#2：指定系所/學院卻沒指定學制 → 預設排除通識課
        clauses.append({"type": "ne", "key": "degree_level", "value": "通識"})

    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"type": "and", "filters": clauses}


# ─────────────────────────── extract_slots ───────────────────────────


class _Slots(BaseModel):
    """extract_slots 的 structured output schema（原文，未正規化）。"""
    department: Optional[str] = None
    college: Optional[str] = None
    degree_level: Optional[str] = None


_EMPTY_SLOTS = {"department": None, "college": None, "degree_level": None}


async def extract_slots(query: str) -> dict:
    """從查詢文字（通常是 condense 後的獨立問句）抽出使用者「明確指定」的系所/學院/學制原文，
    再用 normalize_department/normalize_college/normalize_degree 正規化成 canonical 值。

    使用 OpenAI Responses API structured output（responses.parse + _Slots，
    照抄 backend/qa.py condense_question 的呼叫法，避免 SDK 版本參數不符）。
    抽不到、output_parsed 為 None、或任何例外（含 OPENAI_API_KEY 未設）→ 全部回 None，
    不可讓 slot 抽取拖垮查詢（fail open：退回無 filter 的全庫檢索）。
    """
    from backend.openai_client import get_client
    from backend.recommend import OPENAI_MODEL

    client = get_client()
    if client is None:
        return dict(_EMPTY_SLOTS)

    try:
        resp = await client.responses.parse(
            model=OPENAI_MODEL,
            input=[
                {
                    "role": "system",
                    "content": (
                        "從使用者的課程查詢中，抽出使用者「明確指定」的系所、學院、學制（大學部/碩士/博士/"
                        "研究所/通識）原文。只抽使用者明確提到的，沒提到的欄位一律填 null，不要臆測或補全。"
                        "使用台灣慣用繁體中文。"
                    ),
                },
                {"role": "user", "content": query},
            ],
            text_format=_Slots,
        )
        parsed = resp.output_parsed
        if parsed is None:
            return dict(_EMPTY_SLOTS)
    except Exception:
        return dict(_EMPTY_SLOTS)

    return {
        "department": normalize_department(parsed.department),
        "college": normalize_college(parsed.college),
        "degree_level": normalize_degree(parsed.degree_level),
    }
