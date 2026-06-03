# backend/recommend.py
from __future__ import annotations
import json
import time
from pathlib import Path
from google import genai
from google.genai import types
from pydantic import BaseModel

_BASE = Path(__file__).parent
_CAREERS: dict | None = None
_COURSES_META: dict | None = None


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

class _CourseItem(BaseModel):
    course_id: str
    reason: str

class _Groups(BaseModel):
    core: list[_CourseItem]
    supporting: list[_CourseItem]
    extended: list[_CourseItem]

class _Stage2Output(BaseModel):
    groups: _Groups


def stage1_retrieve(
    client: genai.Client, store_name: str, career: str, skills: list[str]
) -> list[dict]:
    """Stage 1: Use Gemini File Search to retrieve top 15 candidate courses."""
    skill_str = "、".join(skills)
    prompt = (
        f"職涯目標：{career}\n"
        f"所需技能：{skill_str}\n\n"
        "請從課程知識庫找出最相關的 15 門課程。\n"
        "每門課必須回傳：\n"
        "- course_id：9位數課程代號（如 000211012），出現在文件「課程代號:」欄位\n"
        "- course_name：課程名稱\n"
        "- relevance：與職涯目標的相關原因（一句）\n\n"
        '回傳 JSON：[{"course_id": "xxx", "course_name": "xxx", "relevance": "xxx"}]'
    )
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            tools=[
                types.Tool(
                    file_search=types.FileSearch(
                        file_search_store_names=[store_name]
                    )
                )
            ],
        ),
    )
    return json.loads(resp.text)


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
        f"規則：course_id 前6碼相同者只推薦一次。\n"
        f"每門課的 reason 需具體說明與「{career}」目標的關聯（一句話）。"
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
    client: genai.Client, store_name: str, career: str
) -> dict:
    """Full two-stage pipeline. Returns RecommendResponse-compatible dict."""
    t0 = time.monotonic()
    careers = load_careers()
    meta = load_courses_meta()

    skills = careers[career]["skills"]

    candidates = stage1_retrieve(client, store_name, career, skills)
    stage2 = stage2_group(client, career, skills, candidates)

    def process_group(items: list[_CourseItem]) -> list[dict]:
        raw = [{"course_id": i.course_id, "reason": i.reason} for i in items]
        deduped = deduplicate_by_prefix(raw)
        return join_metadata(deduped, meta)

    latency_ms = int((time.monotonic() - t0) * 1000)

    return {
        "career": career,
        "groups": {
            "core": process_group(stage2.groups.core),
            "supporting": process_group(stage2.groups.supporting),
            "extended": process_group(stage2.groups.extended),
        },
        "latency_ms": latency_ms,
    }
