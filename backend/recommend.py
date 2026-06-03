# backend/recommend.py
from __future__ import annotations
import json
import random
import re
import time
from pathlib import Path
from google import genai
from google.genai import types
from pydantic import BaseModel

_BASE = Path(__file__).parent
_CAREERS: dict | None = None
_COURSES_META: dict | None = None


# --- 多樣性參數（跨次輪替）---
POOL_SIZE = 40       # stage1 檢索候選池大小
ANCHOR_COUNT = 4     # 每次必留的最相關門數（保品質）
SAMPLE_SIZE = 18     # 送進 stage2 的候選數


def sample_candidates(
    candidates: list[dict],
    seed: int,
    anchor_count: int = ANCHOR_COUNT,
    sample_size: int = SAMPLE_SIZE,
) -> list[dict]:
    """從候選池選出送往 stage2 的子集，達成跨次輪替。

    - 前 anchor_count 門（最相關）一律保留。
    - 其餘候選用 random.Random(seed) 洗牌後補滿到 sample_size 門。
    - 候選數 <= sample_size → 原樣回傳（不抽樣）。
    - 相同 seed → 結果完全相同（可重現）。
    """
    if len(candidates) <= sample_size:
        return list(candidates)
    anchors = candidates[:anchor_count]
    rest = list(candidates[anchor_count:])
    random.Random(seed).shuffle(rest)
    return anchors + rest[: sample_size - anchor_count]


def extract_json_array(text: str) -> list[dict]:
    """Extract a JSON array from text that may be wrapped in markdown fences
    or surrounded by prose. Returns [] if no valid array found."""
    if not text or not text.strip():
        return []
    # Try direct parse first
    stripped = text.strip()
    # Strip markdown code fences if present
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL)
    if fence_match:
        stripped = fence_match.group(1).strip()
    # Find the first [ ... ] array
    start = stripped.find("[")
    end = stripped.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        result = json.loads(stripped[start : end + 1])
        return result if isinstance(result, list) else []
    except json.JSONDecodeError:
        return []


def load_careers() -> dict:
    global _CAREERS
    if _CAREERS is None:
        _CAREERS = json.loads((_BASE / "career_skills.json").read_text(encoding="utf-8"))
    return _CAREERS


def load_courses_meta() -> dict:
    global _COURSES_META
    if _COURSES_META is None:
        path = _BASE / "courses_meta.json"
        _COURSES_META = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return _COURSES_META


def deduplicate_by_prefix(courses: list[dict]) -> list[dict]:
    """Remove duplicates where the first 6 digits of course_id match."""
    seen: set[str] = set()
    result = []
    for c in courses:
        prefix = c["course_id"][:6]
        if prefix not in seen:
            seen.add(prefix)
            result.append(c)
    return result


def join_metadata(raw_courses: list[dict], meta: dict) -> list[dict]:
    """Enrich course list with metadata. Skips courses not found in meta."""
    result = []
    for c in raw_courses:
        m = meta.get(c["course_id"])
        if not m:
            continue
        result.append({
            "course_id": c["course_id"],
            "name": m["name"],
            "department": m["department"],
            "teacher": m["teacher"],
            "credits": m["credits"],
            "reason": c["reason"],
            "syllabus_url": m["syllabus_url"],
        })
    return result


# --- Gemini Stage 2 Schema ---

class _ReasonPoint(BaseModel):
    term: str       # 粗體關鍵詞，2-6 字
    detail: str     # 該關鍵詞的說明，一句

class _CourseItem(BaseModel):
    course_id: str
    reason_lead: str               # 總述句，一句
    reason_points: list[_ReasonPoint]   # 2-3 個結構化重點

class _Groups(BaseModel):
    core: list[_CourseItem]
    supporting: list[_CourseItem]
    extended: list[_CourseItem]

class _Stage2Output(BaseModel):
    groups: _Groups


def derive_skills_for_career(client: genai.Client, career: str) -> list[str] | None:
    """為清單外職涯用 LLM 推導『可轉移／學術可教』技能關鍵字。回 None 表示非真實職涯。"""
    prompt = (
        f"使用者輸入的職涯目標：「{career}」\n\n"
        "請判斷這是否為一個真實的職涯/工作。若不是（例如亂打的字），回傳空陣列 []。\n"
        "若是，請推導 5-8 個此職涯所需、且大學課程可能教授的『可轉移能力』關鍵字"
        "（聚焦學術可教的能力，如管理、溝通、公共衛生、資料分析；避免純體力或無法在課堂教的技能）。\n"
        '只回傳 JSON 陣列，例：["公共衛生","基礎管理","人際溝通"]'
    )
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config={"response_mime_type": "application/json"},
    )
    try:
        skills = json.loads(resp.text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(skills, list) or not skills:
        return None
    return [str(s) for s in skills][:8]


def stage1_retrieve(
    client: genai.Client, store_name: str, career: str, skills: list[str]
) -> list[dict]:
    """Stage 1: Use Gemini File Search to retrieve top 15 candidate courses."""
    skill_str = "、".join(skills)
    prompt = (
        f"職涯目標：{career}\n"
        f"所需技能：{skill_str}\n\n"
        "請從課程知識庫找出最相關的 "
        f"{POOL_SIZE} 門課程。\n"
        "每門課必須回傳：\n"
        "- course_id：9位數課程代號（如 000211012），出現在文件「課程代號:」欄位\n"
        "- course_name：課程名稱\n"
        "- relevance：與職涯目標的相關原因（一句）\n\n"
        '回傳 JSON：[{"course_id": "xxx", "course_name": "xxx", "relevance": "xxx"}]\n'
        "若知識庫中沒有任何課程與這些技能真正相關，請回傳空陣列 []，不要硬湊不相關的課。\n"
    )
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[
                types.Tool(
                    file_search=types.FileSearch(
                        file_search_store_names=[store_name]
                    )
                )
            ],
        ),
    )
    return extract_json_array(resp.text)


def stage2_group(
    client: genai.Client, career: str, skills: list[str], candidates: list[dict]
) -> _Stage2Output:
    """Stage 2: Group 15 candidates into core/supporting/extended with reasons."""
    skill_str = "、".join(skills)
    candidates_text = "\n".join(
        f"{i + 1}. [{c['course_id']}] {c.get('course_name', '')} — {c.get('relevance', '')}"
        for i, c in enumerate(candidates)
    )
    prompt = (
        f"職涯目標：{career}\n"
        f"核心技能：{skill_str}\n\n"
        f"以下是 {len(candidates)} 門候選課程：\n{candidates_text}\n\n"
        "請選出最推薦的 10 門課，分三組：\n"
        "- core（核心技能）：3-4門，直接對應職涯核心能力\n"
        "- supporting（輔助技能）：3-4門，強化周邊能力\n"
        "- extended（延伸視野）：2-3門，跨域拓展\n\n"
        f"規則：course_id 前6碼相同者只推薦一次。\n\n"
        f"每門課的推薦理由需結構化說明與「{career}」目標的關聯：\n"
        "- reason_lead：一句總述，點出這門課對該職涯的核心價值\n"
        "- reason_points：2-3 個重點，每個含 term（2-6字粗體關鍵詞，如「需求分析」）"
        "與 detail（一句具體說明該關鍵詞如何對應職涯能力）"
    )
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_Stage2Output,
        ),
    )
    return resp.parsed


def build_recommendation(
    client: genai.Client, store_name: str, career: str, seed: int = 0, skills: list[str] | None = None
) -> dict:
    """Full two-stage pipeline. Returns RecommendResponse-compatible dict."""
    t0 = time.monotonic()
    careers = load_careers()
    meta = load_courses_meta()

    if skills is None:
        if career not in careers:
            raise ValueError(f"Unknown career: {career}")
        skills = careers[career]["skills"]

    candidates = stage1_retrieve(client, store_name, career, skills)
    if not candidates:
        raise ValueError("Stage 1 returned no candidate courses")
    # 從候選池抽樣，達成跨次輪替多樣性
    candidates = sample_candidates(candidates, seed)
    stage2 = stage2_group(client, career, skills, candidates)

    def process_group(items: list[_CourseItem]) -> list[dict]:
        raw = [{
            "course_id": i.course_id,
            "reason": {
                "lead": i.reason_lead,
                "points": [{"term": p.term, "detail": p.detail} for p in i.reason_points],
            },
        } for i in items]
        deduped = deduplicate_by_prefix(raw)
        return join_metadata(deduped, meta)

    groups = {
        "core": process_group(stage2.groups.core),
        "supporting": process_group(stage2.groups.supporting),
        "extended": process_group(stage2.groups.extended),
    }
    latency_ms = int((time.monotonic() - t0) * 1000)

    return {
        "career": career,
        "groups": groups,
        "latency_ms": latency_ms,
        "seed": seed,
    }


def build_recommendation_instrumented(
    client: genai.Client, store_name: str, career: str, seed: int = 0, skills: list[str] | None = None
) -> tuple[dict, int]:
    """Same as build_recommendation but also returns stage1 candidate count."""
    t0 = time.monotonic()
    careers = load_careers()
    meta = load_courses_meta()
    if skills is None:
        if career not in careers:
            raise ValueError(f"Unknown career: {career}")
        skills = careers[career]["skills"]

    candidates = stage1_retrieve(client, store_name, career, skills)
    stage1_count = len(candidates)
    if not candidates:
        raise ValueError("Stage 1 returned no candidate courses")
    # 從候選池抽樣，達成跨次輪替多樣性
    candidates = sample_candidates(candidates, seed)
    stage2 = stage2_group(client, career, skills, candidates)

    def process_group(items):
        raw = [{
            "course_id": i.course_id,
            "reason": {
                "lead": i.reason_lead,
                "points": [{"term": p.term, "detail": p.detail} for p in i.reason_points],
            },
        } for i in items]
        return join_metadata(deduplicate_by_prefix(raw), meta)

    groups = {
        "core": process_group(stage2.groups.core),
        "supporting": process_group(stage2.groups.supporting),
        "extended": process_group(stage2.groups.extended),
    }
    latency_ms = int((time.monotonic() - t0) * 1000)
    result = {"career": career, "groups": groups, "latency_ms": latency_ms, "seed": seed}
    return result, stage1_count
