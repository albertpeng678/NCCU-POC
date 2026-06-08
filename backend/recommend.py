# backend/recommend.py
from __future__ import annotations
import asyncio
import json
import logging
import os
import random
import re
import time
from pathlib import Path
from google import genai
from google.genai import types
from google.genai import errors as genai_errors
from openai import APIError as OpenAIAPIError
from pydantic import BaseModel

from backend.retrieval_openai import search_skill

logger = logging.getLogger(__name__)

_BASE = Path(__file__).parent
_CAREERS: dict | None = None
_COURSES_META: dict | None = None
_NAME_INDEX: dict | None = None


# --- 多樣性參數（跨次輪替）---
# 生成模型：維持 gemini-2.5-flash（品質優先；flash-lite 雖快但品質低約 10%，使用者要求保留原模型）
_GEN_MODEL = "gemini-2.5-flash"
POOL_SIZE = 24       # stage1 檢索候選池大小（降輸出量→降延遲；仍足夠多樣性）
ANCHOR_COUNT = 4     # 每次必留的最相關門數（保品質）
SAMPLE_SIZE = 14     # 送進 stage2 的候選數

# --- fan-out 並行檢索參數（推薦提速重構）---
FANOUT_TOP_K = 8          # 每支 file_search 的 chunk 上限（壓 retrieved 量→壓 thinking）
FANOUT_PER_SKILL = 5      # 每技能 prompt 要求的課數
POOL_TARGET = 30          # 合併池上限（round-robin 後截斷前 N）
DEFAULT_BATCH_SIZE = 10   # 前端每批顯示數（後端給預設）

# --- OpenAI 遷移參數 ---
REC_SCORE_THRESHOLD = 0.0   # vector store 檢索分數門檻（0.0 = 不過濾）
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4-mini")


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


def deduplicate_by_name(courses: list[dict], name_key: str) -> list[dict]:
    """依課名去重（保留首次出現），避免跨掛同名課（同名不同 course_id）重複。"""
    seen: set[str] = set()
    result = []
    for c in courses:
        name = c.get(name_key)
        if name in seen:
            continue
        seen.add(name)
        result.append(c)
    return result


def build_name_index(meta: dict) -> dict:
    """從 meta 建 {course_name: course_id} 索引（首次出現優先）。

    用於把 stage1 抄錯的 course_id 透過 course_name 修正回真實 id。
    同名不同 id 時保留首次出現（依 meta 插入序）。
    """
    idx: dict[str, str] = {}
    for cid, m in meta.items():
        name = m.get("name")
        if name and name not in idx:
            idx[name] = cid
    return idx


def correct_candidate_ids(candidates: list[dict], meta: dict) -> list[dict]:
    """修正 stage1 抄錯的 course_id：

    若 candidate 的 course_id 不在 meta，但 course_name 在 name 索引中，
    則用真實 course_id 取代。查不到名稱者維持原樣（後續分組時才丟棄）。
    回傳新 list（不就地改 input）。
    """
    name_index = build_name_index(meta)
    fixed: list[dict] = []
    for c in candidates:
        cid = c.get("course_id")
        if cid not in meta:
            real = name_index.get(c.get("course_name"))
            if real:
                c = {**c, "course_id": real}
        fixed.append(c)
    return fixed


def merge_fanout_results(
    per_skill_results: list[list[dict]], meta: dict
) -> list[dict]:
    """把多支 fan-out 結果 round-robin 交錯合併成單一候選池。

    步驟：
    1. round-robin 交錯（技能1[0], 技能2[0], …, 技能1[1], …）以保多樣，
       避免單技能霸佔池頭。
    2. correct_candidate_ids：用 course_name 修正抄錯的 course_id。
    3. deduplicate_by_name：去跨掛同名課（先到保留）。
    4. deduplicate_by_prefix：去前6碼相同（同課不同班次）。
    5. 截斷至 POOL_TARGET。
    回傳新 list（不就地改 input）。
    """
    interleaved: list[dict] = []
    if per_skill_results:
        max_len = max((len(r) for r in per_skill_results), default=0)
        for col in range(max_len):
            for r in per_skill_results:
                if col < len(r):
                    interleaved.append(r[col])
    fixed = correct_candidate_ids(interleaved, meta)
    deduped = deduplicate_by_name(fixed, "course_name")
    deduped = deduplicate_by_prefix(deduped)
    return deduped[:POOL_TARGET]


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


class _RankedItem(BaseModel):
    course_id: str
    group: str                          # core / supporting / extended
    reason_lead: str
    reason_points: list[_ReasonPoint]

class _RankedOutput(BaseModel):
    courses: list[_RankedItem]          # 依推薦強度由高到低排序（rank = index）


# --- OpenAI structured-output helper ---

class _DerivedSkills(BaseModel):
    """Schema for derive_skills_for_career_async via _openai_structured."""
    skills: list[str]


async def _openai_structured(client, system: str, user: str, schema_model):
    """呼叫 OpenAI Responses API 做結構化輸出，回傳 output_parsed（schema_model 實例）。

    API: client.responses.parse(model=OPENAI_MODEL, input=[...], text_format=schema_model)
    output_parsed 在模型 refusal / token 上限時會是 None → 拋 ValueError（防下游 AttributeError）。
    """
    resp = await client.responses.parse(
        model=OPENAI_MODEL,
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        text_format=schema_model,
    )
    parsed = resp.output_parsed
    if parsed is None:
        raise ValueError(
            f"structured output None (refusal/token-limit); output_text={resp.output_text!r}"
        )
    return parsed


def build_groups(
    stage2: _Stage2Output, candidates: list[dict], meta: dict
) -> dict[str, list[dict]]:
    """把 stage2 分組結果轉成最終 group dict，含 course_id 復原 + 去重。

    對每門 stage2 課程：
    1. 若 course_id 不在 meta，嘗試用「與某 candidate 共享前 6 碼」復原成該
       candidate 的真實 id（前6碼=系所+課號，後3碼=班次；已先經
       correct_candidate_ids 修正的 candidate id 視為可信來源）。
    2. join_metadata 補資料（仍查無 → 丟棄）。
    保留原本行為：組內 deduplicate_by_prefix、跨組依課名去重（core 優先）。
    並印出可觀測 log（stage2 回 N / meta 命中 / 前綴復原 / 最終丟棄）量化此 bug。
    """
    # 候選的真實 course_id 集合（已經 correct_candidate_ids 修正過）
    candidate_ids = {c["course_id"] for c in candidates if c.get("course_id")}
    # 前6碼 → 真實 id（首次出現優先，與其它去重一致）
    prefix_to_id: dict[str, str] = {}
    for cid in (c["course_id"] for c in candidates if c.get("course_id")):
        prefix_to_id.setdefault(cid[:6], cid)

    seen_names: set[str] = set()
    counts = {"stage2": 0, "hit": 0, "prefix_recovered": 0, "dropped": 0}

    def _one(items: list[_CourseItem]) -> list[dict]:
        raw = []
        for i in items:
            counts["stage2"] += 1
            cid = i.course_id
            if cid not in meta:
                # 前綴復原：找共享前6碼的 candidate 真實 id
                recovered = prefix_to_id.get(cid[:6])
                if recovered and recovered in meta:
                    cid = recovered
                    counts["prefix_recovered"] += 1
            raw.append({
                "course_id": cid,
                "reason": {
                    "lead": i.reason_lead,
                    "points": [{"term": p.term, "detail": p.detail} for p in i.reason_points],
                },
            })
        enriched = join_metadata(deduplicate_by_prefix(raw), meta)
        counts["hit"] += len(enriched)
        out = []
        for c in enriched:
            if c["name"] in seen_names:
                continue
            seen_names.add(c["name"])
            out.append(c)
        return out

    groups = {
        "core": _one(stage2.groups.core),
        "supporting": _one(stage2.groups.supporting),
        "extended": _one(stage2.groups.extended),
    }
    counts["dropped"] = counts["stage2"] - counts["hit"]
    logger.info(
        "build_groups: stage2=%d meta_hit=%d name_corrected_candidates=%d "
        "prefix_recovered=%d dropped=%d",
        counts["stage2"], counts["hit"], len(candidate_ids),
        counts["prefix_recovered"], counts["dropped"],
    )
    return groups


def build_ranked_courses(
    ranked: _RankedOutput, candidates: list[dict], meta: dict
) -> list[dict]:
    """把 annotate-pool 的扁平 ranked 輸出轉成最終扁平課程清單。

    對每門課（依 ranked.courses 順序，順序即 rank）：
    1. course_id 不在 meta → 用『與某 candidate 共享前6碼』前綴復原成真實 id。
    2. join_metadata 補資料（仍查無 → 丟棄、不帶 None）。
    3. 前6碼去重（deduplicate_by_prefix）+ 跨課依課名去重（首次保留）。
    保留 group 標籤與 rank（去重/丟棄後重新編號 0..N-1，連續無洞）。
    """
    prefix_to_id: dict[str, str] = {}
    for cid in (c["course_id"] for c in candidates if c.get("course_id")):
        prefix_to_id.setdefault(cid[:6], cid)

    raw: list[dict] = []
    for item in ranked.courses:
        cid = item.course_id
        if cid not in meta:
            recovered = prefix_to_id.get(cid[:6])
            if recovered and recovered in meta:
                cid = recovered
        raw.append({
            "course_id": cid,
            "group": item.group,
            "reason": {
                "lead": item.reason_lead,
                "points": [{"term": p.term, "detail": p.detail}
                           for p in item.reason_points],
            },
        })

    # 前6碼去重（保留首次出現＝較高 rank）
    raw = deduplicate_by_prefix(raw)

    # join metadata（缺 → 丟棄），保留 group/reason
    enriched: list[dict] = []
    for c in raw:
        m = meta.get(c["course_id"])
        if not m:
            continue
        enriched.append({
            "course_id": c["course_id"],
            "name": m["name"],
            "department": m["department"],
            "teacher": m["teacher"],
            "credits": m["credits"],
            "group": c["group"],
            "reason": c["reason"],
            "syllabus_url": m["syllabus_url"],
        })

    # 跨課依課名去重（首次＝較高 rank 保留）
    seen_names: set[str] = set()
    out: list[dict] = []
    for c in enriched:
        if c["name"] in seen_names:
            continue
        seen_names.add(c["name"])
        out.append(c)

    # 重新編號 rank（連續、無洞）
    for i, c in enumerate(out):
        c["rank"] = i

    logger.info(
        "build_ranked_courses: ranked_in=%d final=%d",
        len(ranked.courses), len(out),
    )
    return out


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
        model=_GEN_MODEL,
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "thinking_config": types.ThinkingConfig(thinking_budget=0),  # 關 thinking 降延遲
        },
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
        model=_GEN_MODEL,
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
        f"每門課的推薦理由需簡潔說明與「{career}」目標的關聯：\n"
        "- reason_lead：一句總述（25 字內），點出核心價值\n"
        "- reason_points：恰 2 個重點，每個含 term（2-6字粗體關鍵詞，如「需求分析」）"
        "與 detail（簡短一句，20 字內，說明如何對應職涯能力）"
    )
    resp = client.models.generate_content(
        model=_GEN_MODEL,
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
    """Full fan-out pipeline. Returns RecommendResponse-compatible dict（扁平 courses）。"""
    result, _ = build_recommendation_instrumented(
        client, store_name, career, seed, skills=skills
    )
    return result


async def build_recommendation_instrumented_async(
    client, vs_id: str, career: str, seed: int = 0, skills: list[str] | None = None
) -> tuple[dict, int]:
    """fan-out + 整池標註 pipeline（回傳 result, pool_count）。**async 入口（POST /recommend 用）**。

    直接 await、跑在呼叫端 event loop —— **不可改用 asyncio.run**：worker thread 的臨時 loop 跑完即關。
    seed 保留於回應（前端續池用新 seed 呼叫；多樣性由前端分頁 + 續池新 seed 達成）。
    client: AsyncOpenAI（OpenAI 遷移後）。vs_id: OpenAI vector store id。
    """
    t0 = time.monotonic()
    careers = load_careers()
    meta = load_courses_meta()
    if skills is None:
        if career not in careers:
            raise ValueError(f"Unknown career: {career}")
        skills = careers[career]["skills"]

    candidates = await fanout_retrieve_async(client, vs_id, career, skills)
    if not candidates:
        raise ValueError("Stage 1 returned no candidate courses")
    ranked = await stage2_annotate_pool_async(client, career, skills, candidates)
    courses = build_ranked_courses(ranked, candidates, meta)
    pool_count = len(candidates)

    latency_ms = int((time.monotonic() - t0) * 1000)
    result = {
        "career": career,
        "courses": courses,
        "batch_size": DEFAULT_BATCH_SIZE,
        "latency_ms": latency_ms,
        "seed": seed,
    }
    return result, pool_count


def build_recommendation_instrumented(
    client, vs_id: str, career: str, seed: int = 0, skills: list[str] | None = None
) -> tuple[dict, int]:
    """同步入口（sync 呼叫端用：scripts / sync 測試）。**伺服器請改用 build_recommendation_instrumented_async**，
    避免 asyncio.run 的臨時 loop 污染共用 client.aio（見上方 async 版說明）。"""
    return asyncio.run(
        build_recommendation_instrumented_async(client, vs_id, career, seed, skills=skills)
    )


# ===== async 版（供 SSE 串流逐階段呼叫；不阻塞 event loop） =====
# 與上方同步版邏輯一致，僅把 client.models.generate_content 改為 await client.aio...

async def derive_skills_for_career_async(
    client, career: str
) -> list[str] | None:
    """async 版 derive_skills_for_career（清單外職涯技能推導）。改用 OpenAI _openai_structured。"""
    system = (
        "你是課程推薦助理。請判斷使用者輸入是否為真實職涯，"
        "若是，推導 5-8 個大學課程可教授的可轉移能力關鍵字；"
        "若不是真實職涯（如亂打的字），回傳 skills=[]。"
    )
    user = (
        f"使用者輸入的職涯目標：「{career}」\n\n"
        "請推導此職涯所需、且大學課程可能教授的『可轉移能力』關鍵字"
        "（聚焦學術可教的能力，如管理、溝通、公共衛生、資料分析；避免純體力或無法在課堂教的技能）。\n"
        '範例：{"skills": ["公共衛生","基礎管理","人際溝通"]}'
    )
    try:
        result = await _openai_structured(client, system, user, _DerivedSkills)
    except Exception as e:
        logger.warning("derive_skills failed for %r: %r", career, e)
        return None
    if not result or not result.skills:
        return None
    return [str(s) for s in result.skills][:8]


async def stage1_retrieve_async(
    client: genai.Client, store_name: str, career: str, skills: list[str]
) -> list[dict]:
    """async 版 stage1_retrieve（File Search 海選候選池）。"""
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
    resp = await client.aio.models.generate_content(
        model=_GEN_MODEL,
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


async def fanout_query_skill_async(
    client, vs_id: str, career: str, skill: str
) -> list[dict]:
    """單一技能 → 一支 OpenAI vector store 檢索，回候選 list。

    改用 search_skill（OpenAI vector stores search API），取代 Gemini File Search。
    解析失敗 / 空輸出 → 回 []（呼叫端用 return_exceptions 容錯）。
    """
    raw = await search_skill(
        client, vs_id, skill,
        top_k=FANOUT_TOP_K,
        score_threshold=REC_SCORE_THRESHOLD,
    )
    # search_skill 回 list[{course_id, score, content}]
    # 轉成 pipeline 期望的 {course_id, course_name, relevance} 形狀。
    # course_name 留空字串：塞 chunk 全文（數百字）會灌爆 stage2 prompt 且讓 dedup_by_name 失效；
    # join_metadata 之後會用 courses_meta.json 補入真實課名。
    seen_ids: set[str] = set()  # Fix D：依 course_id 去重保序（同課多 chunk 只保第一筆）
    candidates = []
    for item in raw:
        cid = item.get("course_id", "")
        if not cid:
            continue
        if cid in seen_ids:
            continue
        seen_ids.add(cid)
        candidates.append({
            "course_id": cid,
            "course_name": "",   # 留空；join_metadata 用 courses_meta.json 填真實課名
            "relevance": skill,  # 檢索技能關鍵字作為 relevance
        })
    return candidates


async def fanout_retrieve_async(
    client, vs_id: str, career: str, skills: list[str]
) -> list[dict]:
    """並行 fan-out 檢索：每技能一支 OpenAI vector store search，asyncio.gather 並行。

    - 單支失敗（raise 503/timeout）或回非 list → 視為 []，不拖垮整體（容錯）。
    - 合併委派 merge_fanout_results（round-robin 交錯 + 修正 id + 去重 + 池上限）。
    - 全部皆空/皆失敗 → 回 []（上層判 no_match / error）。
    """
    meta = load_courses_meta()
    results = await asyncio.gather(
        *(fanout_query_skill_async(client, vs_id, career, s) for s in skills),
        return_exceptions=True,
    )
    per_skill: list[list[dict]] = []
    for r in results:
        if isinstance(r, Exception) or not isinstance(r, list):
            logger.warning("fanout skill query failed/invalid: %r", r)
            per_skill.append([])
        else:
            per_skill.append(r)
    return merge_fanout_results(per_skill, meta)


async def stage2_group_async(
    client: genai.Client, career: str, skills: list[str], candidates: list[dict]
) -> _Stage2Output:
    """async 版 stage2_group（分組 + 生成理由）。"""
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
        f"每門課的推薦理由需簡潔說明與「{career}」目標的關聯：\n"
        "- reason_lead：一句總述（25 字內），點出核心價值\n"
        "- reason_points：恰 2 個重點，每個含 term（2-6字粗體關鍵詞，如「需求分析」）"
        "與 detail（簡短一句，20 字內，說明如何對應職涯能力）"
    )
    resp = await client.aio.models.generate_content(
        model=_GEN_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_Stage2Output,
        ),
    )
    return resp.parsed


async def stage2_annotate_pool_async(
    client, career: str, skills: list[str], candidates: list[dict]
) -> _RankedOutput:
    """整池標註：為池中『每一門』課標 group + 理由，並依推薦強度全域排序。

    取代『選 10 門分三組』；改為『標註整池、扁平 ranked 清單』。
    改用 OpenAI _openai_structured（responses.parse）取代 Gemini generate_content。
    回傳順序即 rank（index 0 = 最推薦）。
    """
    skill_str = "、".join(skills)
    candidates_text = "\n".join(
        f"{i + 1}. [{c['course_id']}] {c.get('course_name', '')} — {c.get('relevance', '')}"
        for i, c in enumerate(candidates)
    )
    system = (
        "你是大學課程推薦助理。請為每門候選課程標註推薦分組與理由，並全域排序。"
    )
    user = (
        f"職涯目標：{career}\n"
        f"核心技能：{skill_str}\n\n"
        f"以下是 {len(candidates)} 門候選課程：\n{candidates_text}\n\n"
        "請為『每一門』候選課程標註，並依與職涯目標的推薦強度由高到低『全域排序』"
        "（courses 陣列第一個 = 最推薦）：\n"
        "- course_id：照抄候選的 9 位數課程代號\n"
        "- group：分類，必為 core（核心，直接對應職涯核心能力）/ "
        "supporting（輔助，強化周邊能力）/ extended（延伸，跨域拓展）三者之一\n"
        f"- reason_lead：一句總述（25 字內），點出與「{career}」的核心關聯\n"
        "- reason_points：恰 2 個重點，每個含 term（2-6字粗體關鍵詞，如「需求分析」）"
        "與 detail（簡短一句，20 字內，說明如何對應職涯能力）\n\n"
        "務必涵蓋每一門候選課，不要遺漏、不要新增不在清單中的課。"
    )
    return await _openai_structured(client, system, user, _RankedOutput)


# ===== SSE 串流產生器：逐階段 yield 事件（誠實對應後端真實工作） =====
# 事件形狀：{"event": "stage"|"result"|"no_match", "data": {...}}
# main.py 負責偵測 is_disconnected 與包成 EventSourceResponse。

_STAGES = [
    (1, "understand"),
    (2, "retrieve"),
    (3, "filter"),
    (4, "compose"),
    (5, "finalize"),
]


def _stage_event(n: int, key: str, status: str) -> dict:
    return {"event": "stage", "data": {"n": n, "key": key, "status": status}}


async def stream_recommendation(
    client, vs_id: str, career: str,
    seed: int = 0, skills: list[str] | None = None,
):
    """逐階段串流推薦（fan-out + 整池標註）。事件序列與舊版一致。

    - retrieve 階段內部 = 並行 fan-out（fanout_retrieve_async，已含去重/修正/池上限）。
    - filter 階段保留為純標記（合併去重已在 fan-out 內完成）。
    - compose 階段 = 整池標註（stage2_annotate_pool_async，改用 OpenAI responses.parse）。
    - finalize 階段 = build_ranked_courses（前綴復原 + join meta + 同名去重）。
    - 最終 result 帶扁平 courses + batch_size。空池行為沿用（清單外→no_match；清單內→error）。
    """
    careers = load_careers()
    meta = load_courses_meta()
    is_open = career not in careers

    # --- 階段 1：understand（決定技能）---
    yield _stage_event(1, "understand", "start")
    if skills is None:
        if career in careers:
            skills = careers[career]["skills"]
        else:
            skills = await derive_skills_for_career_async(client, career)
            if not skills:
                yield {"event": "no_match", "data": {
                    "career": career,
                    "message": f"目前沒有找到對應「{career}」的課程，你可以用問答模式問我相關方向。",
                }}
                return
    yield _stage_event(1, "understand", "done")

    # --- 階段 2：retrieve（並行 fan-out 海選）---
    yield _stage_event(2, "retrieve", "start")
    candidates = await fanout_retrieve_async(client, vs_id, career, skills)
    if not candidates:
        if is_open:
            yield {"event": "no_match", "data": {
                "career": career,
                "message": f"政大課程偏學術，目前沒有找到與「{career}」相關的課程，建議用問答模式探索。",
            }}
            return
        yield {"event": "error", "data": {
            "error_type": "ValueError",
            "message": "Stage 1 returned no candidate courses",
        }}
        return
    yield _stage_event(2, "retrieve", "done")

    # --- 階段 3：filter（純標記：合併去重已於 fan-out 完成）---
    yield _stage_event(3, "filter", "start")
    yield _stage_event(3, "filter", "done")

    # --- 階段 4：compose（整池標註：group + 理由 + 全域排序）---
    yield _stage_event(4, "compose", "start")
    try:
        ranked = await stage2_annotate_pool_async(client, career, skills, candidates)
    except OpenAIAPIError as e:
        # 暫時性 OpenAI API 錯誤（503/429/rate-limit，退避用盡後拋）→ 優雅降級成友善 error 事件。
        # 程式 bug（AttributeError/KeyError/ValueError 等）不在此攔，往上拋給 main.py 的 Sentry（保留觀測性）。
        logger.warning("compose API error: %r", e)
        yield {"event": "error", "data": {
            "error_type": type(e).__name__,
            "message": "AI 服務目前較繁忙，請稍後再試一次。",
        }}
        return
    yield _stage_event(4, "compose", "done")

    # --- 階段 5：finalize（補 metadata + course_id 前綴復原 + 同名去重）---
    yield _stage_event(5, "finalize", "start")
    courses = build_ranked_courses(ranked, candidates, meta)
    result = {
        "career": career,
        "courses": courses,
        "batch_size": DEFAULT_BATCH_SIZE,
        "latency_ms": 0,
        "seed": seed,
    }
    if is_open:
        result.setdefault(
            "notice",
            f"政大沒有直接對應「{career}」的課程，但以下課程能培養相關的可轉移能力：",
        )
    yield _stage_event(5, "finalize", "done")
    yield {"event": "result", "data": result}


async def stream_recommendation_from_budget(career: str, budget: dict, seed: int = 0):
    """命中離線預算 → 瞬間吐完 5 階段(start+done) + result（courses 來自預算），0 次即時 AI。
    讓前端柴犬等候動畫有最短展示、且事件序列與即時路徑完全一致。"""
    for n, key in _STAGES:
        yield _stage_event(n, key, "start")
        yield _stage_event(n, key, "done")
    yield {"event": "result", "data": {
        "career": career,
        "courses": budget["courses"],
        "batch_size": DEFAULT_BATCH_SIZE,
        "latency_ms": 0,
        "seed": seed,
    }}
