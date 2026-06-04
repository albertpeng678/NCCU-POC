# 串流即時進度 UX — 後端實作計畫（SSE）

> 對應 spec：`docs/superpowers/specs/2026-06-04-streaming-progress-ux-design.md`
> 範圍：**僅後端純 Python**（requirements / recommend.py / qa.py / main.py / tests）。前端另有計畫。
> 日期：2026-06-04（Session 2）

**REQUIRED SUB-SKILL: `superpowers:subagent-driven-development`**

---

## Goal

把既有兩端點升級成 SSE 串流，且**完全保留**舊 `POST /recommend`、`POST /qa` 當 fallback：

1. `GET /recommend/stream?career=..&seed=..`：用 SSE 逐階段回報後端真實工作（5 階段 `understand / retrieve / filter / compose / finalize`），最後送完整 `RecommendResponse` JSON（與舊 POST 同形）。
2. `GET /qa/stream?question=..&session_id=..`：用 `client.aio.models.generate_content_stream` + file_search 真串流逐 token 吐字；串流末從 grounding metadata 萃取 citations；沿用防幻覺覆寫。

核心原則（spec）：**誠實**——進度反映真實工作；**不阻塞**——全程 async（`client.aio`），心跳不停擺；**防重連重跑**——迴圈內偵測 `request.is_disconnected()`。

## Architecture

- **recommend.py**：把 stage1/stage2 同步呼叫抽成 async（`stage1_retrieve_async`、`stage2_group_async`、`derive_skills_for_career_async`），舊同步函式保留不動（POST fallback 仍用）。新增一個 **async generator** `stream_recommendation(...)`，在每個真實階段邊界 `yield` 結構化事件 dict（`{"event": "...", "data": {...}}`），main.py 把它轉成 SSE。
- **qa.py**：新增 `build_qa_contents(...)`（多輪歷史 → `contents` list）、`stream_answer(...)`（async generator，逐 chunk yield `token` 事件，串流末 yield `done`）、`extract_course_ids_from_chunks(...)`（從累積的 stream chunks 之 `grounding_metadata` 萃取 course_id，沿用 `should_override_no_results` 防幻覺）。舊 `answer_question`（interactions API）保留不動（POST fallback 用）。
- **main.py**：新增兩個 `GET` 端點，回 `sse_starlette.EventSourceResponse(generator, ping=15)`；generator 內每圈先 `if await request.is_disconnected(): break`；例外用 `sentry_sdk.capture_exception` 後 yield `error` 事件。舊 `POST` 端點原封不動。

## Tech Stack

- FastAPI + `sse-starlette`（新增依賴）。
- `google-genai` 2.7.0：`client.aio.models.generate_content_stream`（async stream）+ `client.aio.models.generate_content`（async 非串流，給 recommend 階段用）。
- 測試：pytest + `pytest-asyncio`（已在 requirements），`fastapi.testclient.TestClient`（沿用 `test_api.py` 風格）。**後端測試一律 mock genai client，不打真 Gemini。**

## SSE 事件協定（契約，前端照同一份對接）

推薦 `GET /recommend/stream?career=..&seed=..`：
```
event: stage     data: {"n":1,"key":"understand","status":"start"}   # 5 階段各 start + done
event: stage     data: {"n":1,"key":"understand","status":"done"}
event: result    data: {<完整 RecommendResponse JSON，與舊 POST 同形>}
event: no_match  data: {"career":"..","message":".."}
event: error     data: {"error_type":"..","message":".."}
: keep-alive                                                          # ping=15 心跳
```
5 階段 key 依序：`understand, retrieve, filter, compose, finalize`。

問答 `GET /qa/stream?question=..&session_id=..`：
```
event: token     data: {"text":"..部分文字.."}    # 多次
event: done      data: {"citations":[..],"followups":[..],"session_id":"..","turn":N}
event: error     data: {"error_type":"..","message":".."}
: keep-alive
```

---

## 共用慣例

- **每個 SSE 事件物件格式**（generator yield 的內部結構，main.py 轉 sse-starlette 的 `{"event","data"}`）：
  ```python
  {"event": "stage", "data": {"n": 1, "key": "understand", "status": "start"}}
  ```
  main.py 把 `data` 用 `json.dumps(..., ensure_ascii=False)` 序列化成 `EventSourceResponse` 要的字串。
- **conventional commit**，每個結尾：
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  ```
- **跑測試指令**（repo root，本機 macOS）：`python -m pytest tests/backend/<file> -q`
- 既有測試 78 passing，每步完成後跑全套確認不回歸：`python -m pytest tests/backend -q`

---

# Task 1 — 加 sse-starlette 依賴

**Files**
- Modify: `C:\side\NCCU-poc\backend\requirements.txt`（行 1-10，於尾端新增一行）

### Step 1.1 寫失敗測試
Create: `C:\side\NCCU-poc\tests\backend\test_streaming_deps.py`
```python
# tests/backend/test_streaming_deps.py
def test_sse_starlette_importable():
    """EventSourceResponse 必須可 import（sse-starlette 已安裝）。"""
    from sse_starlette.sse import EventSourceResponse
    assert EventSourceResponse is not None
```

### Step 1.2 跑看失敗
指令：`python -m pytest tests/backend/test_streaming_deps.py -q`
預期 FAIL：`ModuleNotFoundError: No module named 'sse_starlette'`（collection error）。

### Step 1.3 最小實作
Modify `backend/requirements.txt`，在最後一行 `sentry-sdk[fastapi]>=2.0` 之後新增：
```
sse-starlette>=2.1
```
然後安裝：`python -m pip install "sse-starlette>=2.1"`

### Step 1.4 跑看通過
指令：`python -m pytest tests/backend/test_streaming_deps.py -q`
預期 PASS（1 passed）。

### Step 1.5 commit
```
git add backend/requirements.txt tests/backend/test_streaming_deps.py
git commit -m "build(stream): add sse-starlette dependency

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

# Task 2 — recommend.py 階段函式 async 化（保留同步版）

把 `derive_skills_for_career` / `stage1_retrieve` / `stage2_group` 新增 async 版本，內部改呼叫 `client.aio.models.generate_content`。**舊同步函式不動**（POST fallback 仍用）。async 版與同步版邏輯一致，僅 IO 改 await。

**Files**
- Modify: `C:\side\NCCU-poc\backend\recommend.py`（在 `derive_skills_for_career` 後行 171、`stage1_retrieve` 後行 204、`stage2_group` 後行 238 各新增 async 版；建議全部新增於檔尾 `build_recommendation_instrumented` 之後，行 345 之後）

### Step 2.1 寫失敗測試（async stage 函式存在且呼叫 client.aio）
Create: `C:\side\NCCU-poc\tests\backend\test_recommend_async.py`
```python
# tests/backend/test_recommend_async.py
"""recommend.py 階段函式 async 化：須呼叫 client.aio.models.generate_content（不阻塞）。
mock 一個 fake async client，斷言被 await 到且回傳被正確解析。"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from backend.recommend import (
    derive_skills_for_career_async,
    stage1_retrieve_async,
    stage2_group_async,
)


def _fake_client(text=None, parsed=None):
    """建一個 client.aio.models.generate_content 為 AsyncMock 的假 client。"""
    resp = SimpleNamespace(text=text, parsed=parsed)
    aio = SimpleNamespace(models=SimpleNamespace(
        generate_content=AsyncMock(return_value=resp)))
    return SimpleNamespace(aio=aio)


@pytest.mark.asyncio
async def test_derive_skills_async_parses_json_array():
    client = _fake_client(text='["公共衛生","基礎管理","人際溝通"]')
    skills = await derive_skills_for_career_async(client, "流行病學家")
    assert skills == ["公共衛生", "基礎管理", "人際溝通"]
    client.aio.models.generate_content.assert_awaited_once()


@pytest.mark.asyncio
async def test_derive_skills_async_returns_none_on_garbage():
    client = _fake_client(text="[]")
    skills = await derive_skills_for_career_async(client, "asdfqwer")
    assert skills is None


@pytest.mark.asyncio
async def test_stage1_retrieve_async_extracts_array():
    client = _fake_client(
        text='[{"course_id":"000211012","course_name":"政治學","relevance":"x"}]')
    out = await stage1_retrieve_async(client, "store", "PM", ["分析"])
    assert out[0]["course_id"] == "000211012"
    client.aio.models.generate_content.assert_awaited_once()


@pytest.mark.asyncio
async def test_stage2_group_async_returns_parsed():
    from backend.recommend import _Stage2Output, _Groups
    parsed = _Stage2Output(groups=_Groups(core=[], supporting=[], extended=[]))
    client = _fake_client(parsed=parsed)
    out = await stage2_group_async(client, "PM", ["分析"], [])
    assert out is parsed
    client.aio.models.generate_content.assert_awaited_once()
```

### Step 2.2 跑看失敗
指令：`python -m pytest tests/backend/test_recommend_async.py -q`
預期 FAIL：`ImportError: cannot import name 'derive_skills_for_career_async' from 'backend.recommend'`。

### Step 2.3 最小實作
在 `backend/recommend.py` 檔尾（行 345 `return result, stage1_count` 之後，檔案最末）新增三個 async 函式。它們把對應同步函式的 `client.models.generate_content(...)` 改成 `await client.aio.models.generate_content(...)`，其餘邏輯（prompt、config、解析）完全相同：

```python


# ===== async 版（供 SSE 串流逐階段呼叫；不阻塞 event loop） =====
# 與上方同步版邏輯一致，僅把 client.models.generate_content 改為 await client.aio...

async def derive_skills_for_career_async(
    client: genai.Client, career: str
) -> list[str] | None:
    """async 版 derive_skills_for_career（清單外職涯技能推導）。"""
    prompt = (
        f"使用者輸入的職涯目標：「{career}」\n\n"
        "請判斷這是否為一個真實的職涯/工作。若不是（例如亂打的字），回傳空陣列 []。\n"
        "若是，請推導 5-8 個此職涯所需、且大學課程可能教授的『可轉移能力』關鍵字"
        "（聚焦學術可教的能力，如管理、溝通、公共衛生、資料分析；避免純體力或無法在課堂教的技能）。\n"
        '只回傳 JSON 陣列，例：["公共衛生","基礎管理","人際溝通"]'
    )
    resp = await client.aio.models.generate_content(
        model=_GEN_MODEL,
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "thinking_config": types.ThinkingConfig(thinking_budget=0),
        },
    )
    try:
        skills = json.loads(resp.text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(skills, list) or not skills:
        return None
    return [str(s) for s in skills][:8]


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
```

### Step 2.4 跑看通過
指令：`python -m pytest tests/backend/test_recommend_async.py -q`
預期 PASS（4 passed）。

### Step 2.5 commit
```
git add backend/recommend.py tests/backend/test_recommend_async.py
git commit -m "feat(recommend): async stage funcs (client.aio) for streaming, keep sync versions

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

# Task 3 — recommend.py 串流產生器 `stream_recommendation`

新增一個 async generator，串起 5 個真實階段，每階段前後 yield `stage` 事件，邏輯（dedup / sample / process_group / no_match）沿用 `build_recommendation_instrumented`。它**不直接吃 client.is_disconnected**（那由 main.py 包覆），純產生事件序列。

5 階段對應後端真實工作（spec 第三節）：
1. `understand`：決定技能（清單內查 careers；清單外 `derive_skills_for_career_async`）。技能推不出 → yield `no_match` 後結束。
2. `retrieve`：`stage1_retrieve_async`（海選）。空 → yield `no_match`（清單外）或 `error`（清單內，沿用 ValueError 行為）。
3. `filter`：`deduplicate_by_name` + `sample_candidates`（純程式）。
4. `compose`：`stage2_group_async`（分組 + 理由）。
5. `finalize`：`join_metadata` + 跨組去重 → 組 `result` dict → 若清單外補 `notice` → yield `result`。

**Files**
- Modify: `C:\side\NCCU-poc\backend\recommend.py`（在 Task 2 新增的 async 函式之後，檔尾新增 generator）

### Step 3.1 寫失敗測試
Create: `C:\side\NCCU-poc\tests\backend\test_recommend_stream.py`
```python
# tests/backend/test_recommend_stream.py
"""stream_recommendation async generator：驗證 5 階段事件序列、result/no_match 分支。
mock async stage 函式（不打真 Gemini）。"""
import pytest
from unittest.mock import patch, AsyncMock
from backend.recommend import stream_recommendation, _Stage2Output, _Groups, _CourseItem, _ReasonPoint


def _stage2_with_core(course_id):
    item = _CourseItem(
        course_id=course_id, reason_lead="總述",
        reason_points=[_ReasonPoint(term="分析", detail="拆解問題")],
    )
    return _Stage2Output(groups=_Groups(core=[item], supporting=[], extended=[]))


async def _collect(gen):
    return [ev async for ev in gen]


@pytest.mark.asyncio
async def test_stream_known_career_emits_5_stages_and_result():
    meta = {"000211012": {"name": "政治學", "department": "政治系", "teacher": "蔡",
                          "credits": 3.0, "syllabus_url": "https://x/a"}}
    with patch("backend.recommend.load_courses_meta", return_value=meta), \
         patch("backend.recommend.stage1_retrieve_async",
               new=AsyncMock(return_value=[{"course_id": "000211012",
                                            "course_name": "政治學", "relevance": "x"}])), \
         patch("backend.recommend.stage2_group_async",
               new=AsyncMock(return_value=_stage2_with_core("000211012"))):
        events = await _collect(
            stream_recommendation(client=object(), store_name="s",
                                  career="產品經理(PM)", seed=1, skills=None))

    keys = [e["data"]["key"] for e in events if e["event"] == "stage"]
    assert keys == ["understand", "understand", "retrieve", "retrieve",
                    "filter", "filter", "compose", "compose",
                    "finalize", "finalize"]
    statuses = [e["data"]["status"] for e in events if e["event"] == "stage"]
    assert statuses == ["start", "done"] * 5

    result_events = [e for e in events if e["event"] == "result"]
    assert len(result_events) == 1
    r = result_events[0]["data"]
    assert r["career"] == "產品經理(PM)"
    assert r["groups"]["core"][0]["name"] == "政治學"
    assert r["seed"] == 1


@pytest.mark.asyncio
async def test_stream_open_career_no_skills_emits_no_match():
    with patch("backend.recommend.derive_skills_for_career_async",
               new=AsyncMock(return_value=None)):
        events = await _collect(
            stream_recommendation(client=object(), store_name="s",
                                  career="asdfqwer", seed=1, skills=None))
    assert any(e["event"] == "no_match" for e in events)
    assert not any(e["event"] == "result" for e in events)
    # understand 階段有 start/done，retrieve 不應出現（提前結束）
    keys = [e["data"]["key"] for e in events if e["event"] == "stage"]
    assert "retrieve" not in keys


@pytest.mark.asyncio
async def test_stream_open_career_empty_retrieval_emits_no_match():
    with patch("backend.recommend.derive_skills_for_career_async",
               new=AsyncMock(return_value=["公共衛生"])), \
         patch("backend.recommend.stage1_retrieve_async",
               new=AsyncMock(return_value=[])):
        events = await _collect(
            stream_recommendation(client=object(), store_name="s",
                                  career="清潔工", seed=1, skills=None))
    assert any(e["event"] == "no_match" for e in events)
    assert not any(e["event"] == "result" for e in events)
```

### Step 3.2 跑看失敗
指令：`python -m pytest tests/backend/test_recommend_stream.py -q`
預期 FAIL：`ImportError: cannot import name 'stream_recommendation' from 'backend.recommend'`。

### Step 3.3 最小實作
在 `backend/recommend.py` 檔尾（Task 2 的 `stage2_group_async` 之後）新增：

```python


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
    client: genai.Client, store_name: str, career: str,
    seed: int = 0, skills: list[str] | None = None,
):
    """逐階段串流推薦。skills=None 表示清單內（查 careers）或需即時推導（清單外）。

    呼叫端（main.py）已先判斷清單內/外並可能傳入 skills；此處再兜底：
    - skills 傳入 → 視為已決定（清單外推導好的或清單內查好的）。
    - skills=None 且 career 在 careers → 用靜態技能。
    - skills=None 且 career 不在 careers → derive_skills_for_career_async；推不出 → no_match。
    """
    careers = load_careers()
    meta = load_courses_meta()
    is_open = career not in careers  # 清單外旗標（決定 no_match / notice 行為）

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

    # --- 階段 2：retrieve（File Search 海選）---
    yield _stage_event(2, "retrieve", "start")
    candidates = await stage1_retrieve_async(client, store_name, career, skills)
    if not candidates:
        if is_open:
            yield {"event": "no_match", "data": {
                "career": career,
                "message": f"政大課程偏學術，目前沒有找到與「{career}」相關的課程，建議用問答模式探索。",
            }}
            return
        # 清單內海選空：沿用同步版以 error 回報（與 POST 行為一致）
        yield {"event": "error", "data": {
            "error_type": "ValueError",
            "message": "Stage 1 returned no candidate courses",
        }}
        return
    yield _stage_event(2, "retrieve", "done")

    # --- 階段 3：filter（純程式：去重 + 抽樣）---
    yield _stage_event(3, "filter", "start")
    candidates = deduplicate_by_name(candidates, "course_name")
    candidates = sample_candidates(candidates, seed)
    yield _stage_event(3, "filter", "done")

    # --- 階段 4：compose（分組 + 生成理由）---
    yield _stage_event(4, "compose", "start")
    stage2 = await stage2_group_async(client, career, skills, candidates)
    yield _stage_event(4, "compose", "done")

    # --- 階段 5：finalize（補 metadata + 跨組去重）---
    yield _stage_event(5, "finalize", "start")
    seen_names: set[str] = set()

    def process_group(items):
        raw = [{
            "course_id": i.course_id,
            "reason": {
                "lead": i.reason_lead,
                "points": [{"term": p.term, "detail": p.detail} for p in i.reason_points],
            },
        } for i in items]
        enriched = join_metadata(deduplicate_by_prefix(raw), meta)
        out = []
        for c in enriched:
            if c["name"] in seen_names:
                continue
            seen_names.add(c["name"])
            out.append(c)
        return out

    groups = {
        "core": process_group(stage2.groups.core),
        "supporting": process_group(stage2.groups.supporting),
        "extended": process_group(stage2.groups.extended),
    }
    result = {"career": career, "groups": groups, "latency_ms": 0, "seed": seed}
    if is_open:
        result.setdefault(
            "notice",
            f"政大沒有直接對應「{career}」的課程，但以下課程能培養相關的可轉移能力：",
        )
    yield _stage_event(5, "finalize", "done")
    yield {"event": "result", "data": result}
```

### Step 3.4 跑看通過
指令：`python -m pytest tests/backend/test_recommend_stream.py -q`
預期 PASS（3 passed）。

### Step 3.5 commit
```
git add backend/recommend.py tests/backend/test_recommend_stream.py
git commit -m "feat(recommend): stream_recommendation async generator with 5-stage SSE events

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

# Task 4 — qa.py 多輪歷史 contents 組裝

新增純函式 `build_qa_contents(question, history)`：把先前輪次（最近 2-3 輪 question/answer 原文）+ 本輪問題組成 `generate_content` 的 `contents`（list of role/parts dict）。`history` 形狀為 `[{"question": str, "answer": str}, ...]`（呼叫端從 qa_turn 讀回或前端傳入）。

**Files**
- Modify: `C:\side\NCCU-poc\backend\qa.py`（在 `parse_qa_response` 之後，行 154 之後新增）

### Step 4.1 寫失敗測試
Create: `C:\side\NCCU-poc\tests\backend\test_qa_contents.py`
```python
# tests/backend/test_qa_contents.py
"""build_qa_contents：把多輪歷史（最近 N 輪原文）組成 contents，末尾是本輪問題。"""
from backend.qa import build_qa_contents


def test_single_turn_no_history():
    c = build_qa_contents("我想學資料科學", history=None)
    assert isinstance(c, list)
    assert c[-1]["role"] == "user"
    assert "資料科學" in c[-1]["parts"][0]["text"]


def test_multi_turn_includes_history_in_order():
    history = [
        {"question": "Q1", "answer": "A1"},
        {"question": "Q2", "answer": "A2"},
    ]
    c = build_qa_contents("Q3", history=history)
    roles = [m["role"] for m in c]
    # user, model, user, model, user(本輪)
    assert roles == ["user", "model", "user", "model", "user"]
    assert c[0]["parts"][0]["text"] == "Q1"
    assert c[1]["parts"][0]["text"] == "A1"
    assert "Q3" in c[-1]["parts"][0]["text"]


def test_history_truncated_to_last_3_turns():
    history = [{"question": f"Q{i}", "answer": f"A{i}"} for i in range(6)]
    c = build_qa_contents("Qnow", history=history)
    # 最近 3 輪 = 6 則 history + 1 則本輪 = 7
    assert len(c) == 7
    assert c[0]["parts"][0]["text"] == "Q3"  # Q0..Q2 被截掉


def test_empty_history_list_same_as_none():
    assert build_qa_contents("hi", history=[]) == build_qa_contents("hi", history=None)
```

### Step 4.2 跑看失敗
指令：`python -m pytest tests/backend/test_qa_contents.py -q`
預期 FAIL：`ImportError: cannot import name 'build_qa_contents' from 'backend.qa'`。

### Step 4.3 最小實作
在 `backend/qa.py` 的 `parse_qa_response` 函式之後（行 154 之後）新增：

```python


# 多輪歷史：帶最近 N 輪原文進 contents（取代 interactions 的 previous_interaction_id）
_MAX_HISTORY_TURNS = 3


def build_qa_contents(question: str, history: Optional[list] = None) -> list:
    """把多輪歷史 + 本輪問題組成 generate_content 的 contents。

    history: [{"question": str, "answer": str}, ...]（時間升序）。
    只保留最近 _MAX_HISTORY_TURNS 輪原文；本輪問題用 _PROMPT_TEMPLATE 包裝置於末尾。
    """
    contents: list = []
    if history:
        recent = history[-_MAX_HISTORY_TURNS:]
        for turn in recent:
            q = turn.get("question") or ""
            a = turn.get("answer") or ""
            contents.append({"role": "user", "parts": [{"text": q}]})
            contents.append({"role": "model", "parts": [{"text": a}]})
    contents.append({"role": "user", "parts": [{"text": _PROMPT_TEMPLATE.format(question=question)}]})
    return contents
```

### Step 4.4 跑看通過
指令：`python -m pytest tests/backend/test_qa_contents.py -q`
預期 PASS（4 passed）。

### Step 4.5 commit
```
git add backend/qa.py tests/backend/test_qa_contents.py
git commit -m "feat(qa): build_qa_contents — multi-turn history into contents

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

# Task 5 — qa.py 從 stream chunks 萃取 citations

新增 `extract_course_ids_from_chunks(chunks)`：吃一個累積的 stream chunk list，從每個 chunk 的 `candidates[].grounding_metadata.grounding_chunks[].retrieved_context`（file_search 來源）萃取 9 碼 course_id；找不到結構化來源時，退而掃描累積文字的 9 碼碼。沿用 dedup 保序與「全失敗回 []」韌性（同 `extract_course_ids_from_grounding` 概念）。

**Files**
- Modify: `C:\side\NCCU-poc\backend\qa.py`（在 `extract_course_ids_from_grounding` 之後，行 251 之後新增）

### Step 5.1 寫失敗測試
Create: `C:\side\NCCU-poc\tests\backend\test_qa_chunks.py`
```python
# tests/backend/test_qa_chunks.py
"""extract_course_ids_from_chunks：從 generate_content_stream 的累積 chunks 萃取 citations。
chunk.candidates[].grounding_metadata.grounding_chunks[].retrieved_context.{title,text}
含 9 碼 course_id；無結構化來源則掃描文字。"""
from types import SimpleNamespace
from backend.qa import extract_course_ids_from_chunks


def _chunk_with_grounding(course_id, text=""):
    rc = SimpleNamespace(title=f"課程代號: {course_id}", text="政治學課綱…",
                         uri=None)
    gchunk = SimpleNamespace(retrieved_context=rc)
    gm = SimpleNamespace(grounding_chunks=[gchunk])
    cand = SimpleNamespace(grounding_metadata=gm)
    return SimpleNamespace(text=text, candidates=[cand])


def _text_chunk(text):
    cand = SimpleNamespace(grounding_metadata=None)
    return SimpleNamespace(text=text, candidates=[cand])


def test_extract_from_grounding_chunks():
    chunks = [_chunk_with_grounding("000211012"), _text_chunk("…")]
    assert extract_course_ids_from_chunks(chunks) == ["000211012"]


def test_dedup_preserves_order():
    chunks = [_chunk_with_grounding("000211012"),
              _chunk_with_grounding("000216001"),
              _chunk_with_grounding("000211012")]
    assert extract_course_ids_from_chunks(chunks) == ["000211012", "000216001"]


def test_fallback_scans_text_for_9digit():
    chunks = [_text_chunk("推薦課程代號 000211012 很適合")]
    assert extract_course_ids_from_chunks(chunks) == ["000211012"]


def test_empty_when_nothing():
    assert extract_course_ids_from_chunks([_text_chunk("沒有任何碼")]) == []


def test_robust_to_missing_attrs():
    # chunk 結構殘缺不應炸
    assert extract_course_ids_from_chunks([SimpleNamespace()]) == []
```

### Step 5.2 跑看失敗
指令：`python -m pytest tests/backend/test_qa_chunks.py -q`
預期 FAIL：`ImportError: cannot import name 'extract_course_ids_from_chunks' from 'backend.qa'`。

### Step 5.3 最小實作
在 `backend/qa.py` 的 `extract_course_ids_from_grounding` 之後（行 251 之後）新增：

```python


def _grounding_course_ids_from_chunk(chunk) -> list[str]:
    """從單一 stream chunk 的 grounding_metadata 萃取 9 碼 course_id。"""
    out: list[str] = []
    for cand in getattr(chunk, "candidates", None) or []:
        gm = getattr(cand, "grounding_metadata", None)
        if gm is None:
            continue
        for gc in getattr(gm, "grounding_chunks", None) or []:
            rc = getattr(gc, "retrieved_context", None)
            if rc is None:
                continue
            blob = " ".join(
                str(getattr(rc, attr, "") or "") for attr in ("title", "text", "uri")
            )
            m = re.search(r"課程代號[:：]\s*(\d{9})", blob) or re.search(r"\b(\d{9})\b", blob)
            if m:
                out.append(m.group(1))
    return out


def extract_course_ids_from_chunks(chunks) -> list[str]:
    """從 generate_content_stream 累積的 chunks 萃取 grounded course_id。

    優先用 grounding_metadata.grounding_chunks.retrieved_context；
    無結構化來源時退而掃描累積文字的 9 碼碼。dedup 保序；任何例外回 []。
    """
    try:
        found: list[str] = []
        answer_text = ""
        for chunk in chunks or []:
            answer_text += getattr(chunk, "text", "") or ""
            found.extend(_grounding_course_ids_from_chunk(chunk))

        if not found and answer_text:
            for m in re.finditer(r"\b(\d{9})\b", answer_text):
                found.append(m.group(1))

        seen: set[str] = set()
        unique = []
        for cid in found:
            if cid not in seen:
                seen.add(cid)
                unique.append(cid)
        return unique
    except Exception:
        return []
```

### Step 5.4 跑看通過
指令：`python -m pytest tests/backend/test_qa_chunks.py -q`
預期 PASS（5 passed）。

### Step 5.5 commit
```
git add backend/qa.py tests/backend/test_qa_chunks.py
git commit -m "feat(qa): extract_course_ids_from_chunks — citations from stream grounding metadata

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

# Task 6 — qa.py 串流產生器 `stream_answer`

新增 async generator `stream_answer(client, store_name, question, history)`：
- 用 `await client.aio.models.generate_content_stream(...)` + file_search tool + `_SYSTEM_INSTRUCTION`，`contents = build_qa_contents(...)`。
- `async for chunk in stream`：累積 chunk（給末段 grounding 用），逐 chunk yield `{"event":"token","data":{"text": chunk.text}}`（chunk.text 為空則跳過）。
- 串流結束：`extract_course_ids_from_chunks` → course_ids。yield `{"event":"done","data":{"course_ids":[...], "answer_text": 累積全文}}`（citations 與 meta join、防幻覺覆寫、session/turn 由 main.py 處理；qa.py 只回原料）。

> **注意**：串流的 token 是「含 JSON 包裝與表格的完整 markdown 回答」逐 token；防幻覺與 followup 解析在串流末由 main.py 用累積全文 + course_ids 處理（見 Task 7）。`done` 事件的 `answer_text` 是累積原文，供 main.py 跑 `parse_qa_response` 與 `should_override_no_results`。

**Files**
- Modify: `C:\side\NCCU-poc\backend\qa.py`（在 `answer_question` 之後，行 309 之後新增；同時於檔頭 import 區行 9 後確認 `from google.genai import types` 可用——目前未 import，需新增）

### Step 6.1 寫失敗測試
Create: `C:\side\NCCU-poc\tests\backend\test_qa_stream.py`
```python
# tests/backend/test_qa_stream.py
"""stream_answer async generator：逐 chunk yield token，末尾 yield done（含 course_ids + 累積全文）。
mock client.aio.models.generate_content_stream 為回傳 async iterator 的 AsyncMock。"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from backend.qa import stream_answer


def _chunk(text, course_id=None):
    cands = []
    if course_id:
        rc = SimpleNamespace(title=f"課程代號: {course_id}", text="", uri=None)
        gm = SimpleNamespace(grounding_chunks=[SimpleNamespace(retrieved_context=rc)])
        cands = [SimpleNamespace(grounding_metadata=gm)]
    else:
        cands = [SimpleNamespace(grounding_metadata=None)]
    return SimpleNamespace(text=text, candidates=cands)


async def _aiter(items):
    for it in items:
        yield it


def _fake_stream_client(chunks):
    # generate_content_stream 是 async def 回傳 async iterator → AsyncMock，return_value 為 async gen
    aio = SimpleNamespace(models=SimpleNamespace(
        generate_content_stream=AsyncMock(return_value=_aiter(chunks))))
    return SimpleNamespace(aio=aio)


async def _collect(gen):
    return [ev async for ev in gen]


@pytest.mark.asyncio
async def test_stream_yields_tokens_then_done():
    chunks = [_chunk("政治學"), _chunk(" 很棒", course_id="000211012")]
    client = _fake_stream_client(chunks)
    events = await _collect(stream_answer(client, "store", "問政治學", history=None))

    tokens = [e["data"]["text"] for e in events if e["event"] == "token"]
    assert tokens == ["政治學", " 很棒"]

    done = [e for e in events if e["event"] == "done"]
    assert len(done) == 1
    assert done[0]["data"]["course_ids"] == ["000211012"]
    assert done[0]["data"]["answer_text"] == "政治學 很棒"
    client.aio.models.generate_content_stream.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_skips_empty_text_chunks():
    chunks = [_chunk(""), _chunk("有字"), _chunk(None)]
    client = _fake_stream_client(chunks)
    events = await _collect(stream_answer(client, "store", "q", history=None))
    tokens = [e for e in events if e["event"] == "token"]
    assert len(tokens) == 1
    assert tokens[0]["data"]["text"] == "有字"
```

### Step 6.2 跑看失敗
指令：`python -m pytest tests/backend/test_qa_stream.py -q`
預期 FAIL：`ImportError: cannot import name 'stream_answer' from 'backend.qa'`。

### Step 6.3 最小實作
先在 `backend/qa.py` 檔頭 import 區（行 9 `from google import genai` 之後）新增：
```python
from google.genai import types
```
然後在 `answer_question` 之後（行 309 之後，檔尾）新增：

```python


async def stream_answer(
    client: genai.Client,
    store_name: str,
    question: str,
    history: Optional[list] = None,
):
    """串流回答課程問題（generate_content_stream + file_search）。

    逐 chunk yield {"event":"token","data":{"text":...}}；
    串流末 yield {"event":"done","data":{"course_ids":[...], "answer_text": 累積全文}}。
    多輪歷史由 build_qa_contents 帶入 contents（取代 interactions 的 previous_interaction_id）。
    citations join / 防幻覺覆寫 / session 持久化由呼叫端（main.py）處理。
    """
    contents = build_qa_contents(question, history)
    config = types.GenerateContentConfig(
        system_instruction=_SYSTEM_INSTRUCTION,
        temperature=0.2,
        top_p=0.95,
        max_output_tokens=2048,
        tools=[
            types.Tool(
                file_search=types.FileSearch(
                    file_search_store_names=[store_name]
                )
            )
        ],
    )

    chunks: list = []
    answer_text = ""
    stream = await client.aio.models.generate_content_stream(
        model="gemini-2.5-flash",
        contents=contents,
        config=config,
    )
    async for chunk in stream:
        chunks.append(chunk)
        text = getattr(chunk, "text", None)
        if text:
            answer_text += text
            yield {"event": "token", "data": {"text": text}}

    course_ids = extract_course_ids_from_chunks(chunks)
    yield {"event": "done", "data": {
        "course_ids": course_ids,
        "answer_text": answer_text,
    }}
```

### Step 6.4 跑看通過
指令：`python -m pytest tests/backend/test_qa_stream.py -q`
預期 PASS（2 passed）。

### Step 6.5 commit
```
git add backend/qa.py tests/backend/test_qa_stream.py
git commit -m "feat(qa): stream_answer — generate_content_stream + file_search token streaming

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

# Task 7 — main.py 新增 `GET /recommend/stream`

新增 SSE 端點：query 參數 `career`、`seed`（int，預設隨機）。包覆 `stream_recommendation`，每圈先檢查 `await request.is_disconnected()`（防重連重跑），例外送 Sentry + yield `error` 事件。用 `EventSourceResponse(gen, ping=15)`。**舊 `POST /recommend` 不動。**

> SSE 序列化：sse-starlette 的 `EventSourceResponse` 吃 generator yield `dict(event=..., data=...)`，`data` 需為字串 → 用 `json.dumps(..., ensure_ascii=False)`。`X-Accel-Buffering: no` 由 sse-starlette 自動加（防 proxy 緩衝）；`ping=15` 送 `: keep-alive` 心跳。

**Files**
- Modify: `C:\side\NCCU-poc\backend\main.py`
  - import 區（行 13-32）：新增 `from sse_starlette.sse import EventSourceResponse`、`from fastapi import Request`、`import json`、`from backend.recommend import stream_recommendation`、`from backend.qa import stream_answer, build_qa_contents`（Task 8 用）。
  - 在 `recommend` POST 端點之後（行 143 之後）新增 GET stream 端點。

### Step 7.1 寫失敗測試
Create: `C:\side\NCCU-poc\tests\backend\test_stream_api.py`
```python
# tests/backend/test_stream_api.py
"""SSE 端點整合測試：用 TestClient 收 SSE 文字流，斷言事件協定。
mock backend.main 內的串流產生器（不打真 Gemini）。沿用 test_api.py 的 patch 風格。"""
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient


async def _gen_recommend_ok():
    yield {"event": "stage", "data": {"n": 1, "key": "understand", "status": "start"}}
    yield {"event": "stage", "data": {"n": 1, "key": "understand", "status": "done"}}
    yield {"event": "result", "data": {"career": "產品經理(PM)", "groups":
           {"core": [], "supporting": [], "extended": []}, "latency_ms": 0, "seed": 7}}


async def _gen_recommend_no_match():
    yield {"event": "stage", "data": {"n": 1, "key": "understand", "status": "start"}}
    yield {"event": "no_match", "data": {"career": "asdf", "message": "查無"}}


@pytest.fixture
def client():
    from backend.main import app
    return TestClient(app)


def _events(text):
    """把 SSE 原始文字切成 (event, data) tuples。"""
    out = []
    cur_event = None
    for line in text.splitlines():
        if line.startswith("event:"):
            cur_event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            out.append((cur_event, line[len("data:"):].strip()))
    return out


def test_recommend_stream_emits_stages_and_result(client):
    with patch("backend.main.stream_recommendation", return_value=_gen_recommend_ok()):
        resp = client.get("/recommend/stream?career=產品經理(PM)&seed=7")
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    evs = _events(resp.text)
    names = [e for e, _ in evs]
    assert "stage" in names
    assert "result" in names
    assert '"seed": 7' in resp.text or '"seed":7' in resp.text


def test_recommend_stream_no_match(client):
    with patch("backend.main.stream_recommendation", return_value=_gen_recommend_no_match()):
        resp = client.get("/recommend/stream?career=asdf")
    assert resp.status_code == 200
    evs = _events(resp.text)
    assert any(e == "no_match" for e, _ in evs)
```

### Step 7.2 跑看失敗
指令：`python -m pytest tests/backend/test_stream_api.py::test_recommend_stream_emits_stages_and_result -q`
預期 FAIL：`404 != 200`（端點不存在）；或先 `AttributeError`/import 錯（`stream_recommendation` 未在 backend.main 命名空間）。

### Step 7.3 最小實作
在 `backend/main.py` import 區新增（於行 32 之後）：
```python
import json
from fastapi import Request
from sse_starlette.sse import EventSourceResponse
from backend.recommend import stream_recommendation
from backend.qa import stream_answer
```
在 `recommend` POST 端點之後（行 143 之後）新增：

```python


# ===== SSE 串流端點（保留舊 POST 當 fallback）=====

def _sse(event: str, data: dict) -> dict:
    """把內部事件轉成 sse-starlette EventSourceResponse 接受的格式。"""
    return {"event": event, "data": json.dumps(data, ensure_ascii=False)}


@app.get("/recommend/stream")
async def recommend_stream(request: Request, career: str, seed: int | None = None):
    careers = load_careers()
    resolved_seed = seed if seed is not None else random.randrange(1_000_000)

    # 清單外職涯：技能推導交給 stream_recommendation 內部（會走 understand 階段 + no_match）
    skills = None

    async def event_gen():
        try:
            async for ev in stream_recommendation(
                _client, _STORE_NAME, career, resolved_seed, skills=skills
            ):
                if await request.is_disconnected():
                    break  # 前端已關閉（收到終態 es.close()）→ 中止，勿續燒 Gemini
                yield _sse(ev["event"], ev["data"])
        except Exception as e:
            sentry_sdk.capture_exception(e)
            yield _sse("error", {"error_type": type(e).__name__, "message": str(e)})

    return EventSourceResponse(event_gen(), ping=15)
```

> 註：`load_careers` 已由 `from backend.recommend import (...)` 行 17-20 匯入；若未匯入，於該 import 區加入 `load_careers`。確認後再實作。實際檔案行 18 已匯入 `load_careers, load_courses_meta`，故無需新增。

### Step 7.4 跑看通過
指令：`python -m pytest tests/backend/test_stream_api.py -q`
預期：`test_recommend_stream_*` 兩個 PASS（`test_qa_stream_*` 尚未實作，Task 8 才加；此檔此時只有 recommend 兩測）。

### Step 7.5 commit
```
git add backend/main.py tests/backend/test_stream_api.py
git commit -m "feat(api): GET /recommend/stream SSE endpoint (EventSourceResponse, ping=15, is_disconnected)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

# Task 8 — main.py 新增 `GET /qa/stream`

新增 SSE 端點：query `question`、`session_id`（選）。流程：
1. 解析 session（沿用 `get_session`/`create_session`；無 DB → yield `error`）。
2. 從 `get_session_turns` 讀回歷史 → 轉成 `build_qa_contents` 要的 `[{"question","answer"}]`。
3. `async for ev in stream_answer(...)`：每圈檢查 `is_disconnected`；`token` 事件直接轉送；`done` 事件時：用 `answer_text` 跑 `parse_qa_response`、`extract_citations`（join meta）、`should_override_no_results` 防幻覺覆寫，再 yield `done`（含 citations/followups/session_id/turn）。
4. 串流末持久化 turn（`insert_turn` + `bump_session`）。
5. 例外 → Sentry + `error` 事件。

> **多輪歷史來源**：本案用 DB 既有 `get_session_turns`（含 `question`、`answer` 欄）。若 `answer` 欄為串流前的原文即可。截斷由 `build_qa_contents` 處理（最近 3 輪）。

**Files**
- Modify: `C:\side\NCCU-poc\backend\main.py`（在 `qa` POST 端點之後，行 215 之後新增 GET stream 端點；import 已於 Task 7 補齊）
- Modify: `C:\side\NCCU-poc\tests\backend\test_stream_api.py`（新增 qa stream 測試）

### Step 8.1 寫失敗測試
在 `C:\side\NCCU-poc\tests\backend\test_stream_api.py` 末尾新增：
```python


async def _gen_qa_ok():
    yield {"event": "token", "data": {"text": "政治學"}}
    yield {"event": "token", "data": {"text": " 不錯"}}
    yield {"event": "done", "data": {"course_ids": ["000211012"],
                                     "answer_text": "政治學 **不錯**"}}


def test_qa_stream_tokens_then_done(client):
    meta = {"000211012": {"name": "政治學", "department": "政治系", "teacher": "蔡",
                          "credits": 3.0, "syllabus_url": "https://x/a"}}
    with patch("backend.main.stream_answer", return_value=_gen_qa_ok()), \
         patch("backend.main.get_pool", return_value=object()), \
         patch("backend.main.create_session", new=AsyncMock(return_value="sess-1")), \
         patch("backend.main.get_session_turns", new=AsyncMock(return_value=[])), \
         patch("backend.main.insert_turn", new=AsyncMock(return_value=1)), \
         patch("backend.main.bump_session", new=AsyncMock(return_value=None)), \
         patch("backend.main.load_courses_meta", return_value=meta):
        resp = client.get("/qa/stream?question=問政治學")
    assert resp.status_code == 200
    evs = _events(resp.text)
    names = [e for e, _ in evs]
    assert names.count("token") == 2
    assert "done" in names
    # done 事件含 citations（join 後）與 session_id
    done_data = [d for e, d in evs if e == "done"][0]
    assert "政治學" in done_data
    assert "sess-1" in done_data


def test_qa_stream_hallucination_override(client):
    """citations 空但答案像列課程（含表格） → 覆寫 NO_RESULTS_MESSAGE。"""
    async def _gen_no_cite():
        yield {"event": "token", "data": {"text": "| 課程 | 系所 |\n| --- | --- |\n"}}
        yield {"event": "done", "data": {"course_ids": [],
               "answer_text": "| 課程 | 系所 |\n| --- | --- |\n| 假課 | 假系 |"}}

    with patch("backend.main.stream_answer", return_value=_gen_no_cite()), \
         patch("backend.main.get_pool", return_value=object()), \
         patch("backend.main.create_session", new=AsyncMock(return_value="sess-2")), \
         patch("backend.main.get_session_turns", new=AsyncMock(return_value=[])), \
         patch("backend.main.insert_turn", new=AsyncMock(return_value=1)), \
         patch("backend.main.bump_session", new=AsyncMock(return_value=None)), \
         patch("backend.main.load_courses_meta", return_value={}):
        resp = client.get("/qa/stream?question=亂問")
    evs = _events(resp.text)
    done_data = [d for e, d in evs if e == "done"][0]
    assert "沒有找到" in done_data  # NO_RESULTS_MESSAGE 片段
```

### Step 8.2 跑看失敗
指令：`python -m pytest tests/backend/test_stream_api.py::test_qa_stream_tokens_then_done -q`
預期 FAIL：`404 != 200`（`/qa/stream` 不存在）。

### Step 8.3 最小實作
先確認 main.py import 區已有（Task 7 已加 `stream_answer`）。`get_session_turns`、`create_session`、`get_session`、`insert_turn`、`bump_session` 已於行 29-32 匯入；`load_courses_meta` 已於行 18 匯入；`extract_citations`、`should_override_no_results`、`NO_RESULTS_MESSAGE` 已於行 24-27 匯入；`parse_qa_response` 需新增匯入。

在 main.py import 區行 27 的 qa import block 補上 `parse_qa_response`：
```python
from backend.qa import (
    answer_question, extract_citations,
    should_override_no_results, NO_RESULTS_MESSAGE,
    stream_answer, parse_qa_response,
)
```
（把 Task 7 加的 `from backend.qa import stream_answer` 合併進這個 block，避免重複 import。）

在 `qa` POST 端點之後（行 215 之後）新增：

```python


@app.get("/qa/stream")
async def qa_stream(request: Request, question: str, session_id: str | None = None):
    pool = get_pool()

    # 解析 session（沿用 POST /qa 規則）
    turn_number = 1
    history: list = []
    if session_id:
        sess = await get_session(pool, session_id)
        if sess is None:
            async def _err():
                yield _sse("error", {"error_type": "NotFound", "message": "Session not found"})
            return EventSourceResponse(_err(), ping=15)
        turn_number = (sess.get("turn_count") or 0) + 1
        turns = await get_session_turns(pool, session_id)
        history = [{"question": t.get("question"), "answer": t.get("answer")} for t in turns]
    else:
        session_id = await create_session(pool)
        if session_id is None:
            async def _err():
                yield _sse("error", {"error_type": "ServiceUnavailable",
                                     "message": "Cannot create session (DB unavailable)"})
            return EventSourceResponse(_err(), ping=15)

    async def event_gen():
        meta = load_courses_meta()
        result_dict = None
        error = None
        try:
            async for ev in stream_answer(_client, _STORE_NAME, question, history):
                if await request.is_disconnected():
                    return  # 前端已關閉 → 中止
                if ev["event"] == "token":
                    yield _sse("token", ev["data"])
                elif ev["event"] == "done":
                    parsed = parse_qa_response(ev["data"]["answer_text"])
                    citations = extract_citations(ev["data"]["course_ids"], meta)
                    answer = parsed["answer"]
                    followups = parsed["followup_suggestions"]
                    # 防幻覺：citations 空卻列具體課程 → 覆寫
                    if should_override_no_results(answer, citations):
                        answer = NO_RESULTS_MESSAGE
                        followups = []
                    result_dict = {
                        "answer": answer,
                        "citations_course_ids": ev["data"]["course_ids"],
                        "followup_suggestions": followups,
                        "latency_ms": 0,
                    }
                    yield _sse("done", {
                        "citations": citations,
                        "followups": followups,
                        "session_id": session_id,
                        "turn": turn_number,
                    })
        except Exception as e:
            error = e
            sentry_sdk.capture_exception(e)
            yield _sse("error", {"error_type": type(e).__name__, "message": str(e)})

        # 串流末持久化 turn（fire-and-forget 不影響已送出的事件）
        await insert_turn(pool, session_id, turn_number, question, result_dict, error)
        if result_dict:
            await bump_session(pool, session_id, "")

    return EventSourceResponse(event_gen(), ping=15)
```

> 註：`bump_session` 第三參數原為 `interaction_id`；串流不再有 interaction_id，傳空字串保持 `turn_count` 遞增（last_interaction_id 設空，不影響串流多輪——多輪靠 contents history）。若 schema 不允許空字串，改傳 `None` 並確認 `qa_logger.bump_session` 容忍（目前 SQL 直接寫入，空字串可接受）。

### Step 8.4 跑看通過
指令：`python -m pytest tests/backend/test_stream_api.py -q`
預期 PASS（4 passed：2 recommend + 2 qa）。

### Step 8.5 commit
```
git add backend/main.py tests/backend/test_stream_api.py
git commit -m "feat(api): GET /qa/stream SSE endpoint — token stream, citations join, hallucination guard, turn persist

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

# Task 9 — 全套回歸 + 既有端點不破

確認新增 SSE 不影響既有 78 passing，且舊 `POST /recommend`、`POST /qa` 行為不變。

### Step 9.1 跑全套
指令：`python -m pytest tests/backend -q`
預期 PASS：原 78 + 新增（deps 1 + recommend_async 4 + recommend_stream 3 + qa_contents 4 + qa_chunks 5 + qa_stream 2 + stream_api 4 = 23）≈ 101 passed。

### Step 9.2 若有失敗 → systematic-debugging
任何 RED 用 `superpowers:systematic-debugging` 逐一定位；不可改測試遷就壞實作，除非測試本身寫錯。

### Step 9.3 最終 commit（若 9.1 全綠且無檔案改動則略過）
無須額外 commit；確認 `git status` 乾淨。

---

## 風險與前後端對齊點（給執行 agent 注意）

1. **grounding_metadata 真實結構未經真 Gemini 驗證**：Task 5 的 `retrieved_context.title` 是否真含「課程代號: 9碼」需在 E2E（真後端）核對。退路已備（掃描文字 9 碼）。執行 E2E 時先 `print` 一個真 chunk 的 `grounding_metadata` 結構確認，必要時微調 `_grounding_course_ids_from_chunk`。
2. **多輪歷史 token 成本**：預設帶最近 3 輪原文（spec 開放問題）。若答案很長可能膨脹 prompt；E2E 量到延遲異常時降到 2 輪或加摘要。
3. **`bump_session` 的 interaction_id**：串流不再產生 interaction_id，本計畫傳空字串僅為遞增 turn_count。若日後要嚴格，改 schema 容許 NULL。**與既有 POST /qa 的 session 共用同一張表，但 last_interaction_id 語意不同**——前端切換 stream/POST 模式時 session 可互通，惟 POST fallback 仍依賴 interaction_id 多輪，stream 依賴 contents history，兩者不要混用同一 session 的多輪鏈。
4. **EventSourceResponse data 換行**：`json.dumps` 不含換行，安全。若未來 data 含 `\n` 須注意 SSE 以 `\n\n` 分隔事件——目前皆 JSON 一行，無虞。
5. **CORS**：SSE 端點走既有 CORSMiddleware（已 `allow_methods=["POST","GET"]`），無須改動。前端 `EventSource` 跨網域時需 `withCredentials` 對齊——本案不帶 cookie，預設即可。
6. **proxy 緩衝**：`X-Accel-Buffering: no` 由 sse-starlette 自動加；部署後須 `curl -N` 驗證逐塊到達（spec 第八節），非後端單元測試能涵蓋——列入 E2E 驗收。
7. **TestClient 與 SSE**：`TestClient.get` 會把整條 SSE 流讀完成文字（同步），故測試可解析；但**無法測 `is_disconnected` 中斷**——該行為列入 E2E（前端 `es.close()` 後觀察後端不續打 Gemini）。
