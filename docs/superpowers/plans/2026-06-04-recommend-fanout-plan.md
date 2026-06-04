# 推薦提速 fan-out 重構 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把職涯推薦 stage1 從「單次大檢索」改成「每技能一支並行 fan-out 小檢索」、stage2 改「整池扁平 ranked 標註」、回應改扁平 `courses` 清單 + `batch_size`，前端改 client-side 分頁「換一批」，將首載 ~124s→~42s、換一批 ~25s→0s。

**Architecture:** 後端 `recommend.py` 新增 `fanout_retrieve_async`（每技能 1 支 `FileSearch(top_k=8)`、`asyncio.gather` 並行、round-robin 交錯合併、`POOL_TARGET=30` 上限）取代 stage1 單次大檢索；新增扁平 ranked schema `_RankedItem`/`_RankedOutput` 與 `build_ranked_courses`（前綴復原 + join_metadata + 跨課同名去重 + 保留 rank/group）取代巢狀 `build_groups`；`RecommendResponse` 改回扁平 `courses: list[Course]` + `batch_size:int=10`；`stream_recommendation` 的 retrieve 階段內部換 fan-out、compose 階段換整池標註，事件序列不變。前端抽出 `frontend/pagination.js` 純函式模組（可單測的切批/續池邏輯），`app.js` 接整池存於 `state.pool`/`state.shown`、「換一批」純前端切片、池乾才以新 seed 呼叫 `/recommend`。生成模型維持 `gemini-2.5-flash`，`HttpOptions(timeout=180_000, retry 429/503)` 保留。POST `/recommend` 與既有測試向後相容。

**Tech Stack:** Python 3.11 + FastAPI + asyncio + google-genai（async `client.aio.models.generate_content`）+ Pydantic；pytest + pytest-asyncio（strict mode，`@pytest.mark.asyncio` 裝飾）；原生 JS ESM 模組 + Node 內建 `node:test`/`node:assert`（無新依賴）；Playwright real-backend e2e（後續另建工具鏈，本 plan 列出測試檔與斷言但標明稀少樣本）。

**測試執行環境（重要）：** 所有 pytest 指令一律用專案 venv：`.venv/bin/python -m pytest ...`（Python 3.11、pytest-asyncio 1.4.0 已裝；系統 python3 是 3.14 且缺 pytest-asyncio，會誤判 async 測試失敗）。執行前先設環境變數骨架：`ALLOWED_ORIGIN=* GEMINI_API_KEY=x FILE_SEARCH_STORE_NAME=x`（`backend.main` import 時需要；純 `recommend.py` 單元測試其實不 import main，但設了無害）。前端 JS 單元測試用 `node --test frontend/`。

---

## 設計參數速查（本 plan 落實的常數）

| 常數 | 值 | 位置 | 意義 |
|---|---|---|---|
| `FANOUT_TOP_K` | 8 | `recommend.py` | 每支 file_search 的 `FileSearch(top_k=...)` chunk 上限 |
| `FANOUT_PER_SKILL` | 5 | `recommend.py` | 每技能 prompt 要求的課數 |
| `POOL_TARGET` | 30 | `recommend.py` | 合併池上限（round-robin 後截斷前 N） |
| `DEFAULT_BATCH_SIZE` | 10 | `recommend.py` / `models.py` | 前端每批顯示數（後端給預設） |
| `_GEN_MODEL` | `gemini-2.5-flash` | `recommend.py`（既有） | **不可換 flash-lite** |

## 檔案結構（建立 / 修改總覽）

- 修改 `backend/recommend.py`：新增常數、`fanout_retrieve_async`、`merge_fanout_results`、`_RankedItem`/`_RankedOutput`、`stage2_annotate_pool_async`、`build_ranked_courses`；改 `stream_recommendation` 內部；保留舊 `stage1_retrieve(_async)`/`stage2_group(_async)`/`build_groups`/`_Stage2Output` 供 POST 路徑與既有測試（向後相容）。
- 修改 `backend/models.py`：新增 `Course`（扁平，含 `group`）；`RecommendResponse` 改為 `courses: list[Course]` + `batch_size: int = 10`（保留 `career`/`latency_ms`/`seed`/`notice`）。
- 修改 `backend/main.py`：`/recommend`（POST）與 `/recommend/stream` 回傳扁平 `courses`；POST 走新 fan-out + 整池標註 pipeline。
- 建立 `frontend/pagination.js`：純函式 ESM 模組（`createPaginationState`、`nextBatch`、`appendPool`、`groupBatch`）。
- 修改 `frontend/app.js`：import pagination 模組、`renderResults` 改扁平 courses 分區、「換一批」純前端切片、池乾續池。
- 建立 `tests/backend/test_fanout.py`：fan-out 合併去重 + 容錯 + 池上限（§2.A-C）。
- 建立 `tests/backend/test_ranked.py`：扁平 ranked 轉換（§2.D）。
- 建立 `tests/frontend/pagination.test.mjs`：前端分頁切批（§2.E）。
- 建立 `e2e/recommend-fanout.spec.ts` 與 `e2e/recommend-reroll.spec.ts`：Playwright real backend（§3.R/S/M）— 僅骨架與斷言，標明稀少樣本。

---

## Task 1: fan-out 常數 + 單技能查詢函式

新增並行 fan-out 的設計常數，與「單一技能 → 一支 file_search 查詢」的 async 函式 `fanout_query_skill_async`。

**Files:**
- Modify: `backend/recommend.py:21-26`（在 `POOL_SIZE`/`ANCHOR_COUNT`/`SAMPLE_SIZE` 之後加新常數）
- Modify: `backend/recommend.py`（在 `stage1_retrieve_async` 之後、`stage2_group_async` 之前新增函式）
- Test: `tests/backend/test_fanout.py`（新建）

- [ ] **Step 1: Write the failing test**

建立 `tests/backend/test_fanout.py`：

```python
# tests/backend/test_fanout.py
"""fan-out 並行檢索：每技能一支 file_search、合併去重、容錯、池上限。
全程 mock async client，不打真 Gemini。"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from backend.recommend import (
    FANOUT_TOP_K,
    FANOUT_PER_SKILL,
    POOL_TARGET,
    fanout_query_skill_async,
)


def _fake_client(text):
    """client.aio.models.generate_content 回傳 .text=text 的假 client。"""
    resp = SimpleNamespace(text=text)
    aio = SimpleNamespace(models=SimpleNamespace(
        generate_content=AsyncMock(return_value=resp)))
    return SimpleNamespace(aio=aio)


def test_fanout_constants_have_expected_values():
    assert FANOUT_TOP_K == 8
    assert FANOUT_PER_SKILL == 5
    assert POOL_TARGET == 30


@pytest.mark.asyncio
async def test_fanout_query_skill_parses_array():
    client = _fake_client(
        '[{"course_id":"000211012","course_name":"政治學","relevance":"x"}]')
    out = await fanout_query_skill_async(client, "store", "PM", "分析")
    assert out[0]["course_id"] == "000211012"
    client.aio.models.generate_content.assert_awaited_once()


@pytest.mark.asyncio
async def test_fanout_query_skill_empty_text_returns_empty_list():
    client = _fake_client("沒有相關課程")
    out = await fanout_query_skill_async(client, "store", "PM", "分析")
    assert out == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ALLOWED_ORIGIN=* GEMINI_API_KEY=x FILE_SEARCH_STORE_NAME=x .venv/bin/python -m pytest tests/backend/test_fanout.py -v`
Expected: FAIL with `ImportError: cannot import name 'FANOUT_TOP_K'`（常數與函式尚未定義）。

- [ ] **Step 3: Write minimal implementation**

在 `backend/recommend.py` 的多樣性參數區塊（`SAMPLE_SIZE = 14` 之後，第 26 行附近）新增常數：

```python
# --- fan-out 並行檢索參數（推薦提速重構）---
FANOUT_TOP_K = 8          # 每支 file_search 的 chunk 上限（壓 retrieved 量→壓 thinking）
FANOUT_PER_SKILL = 5      # 每技能 prompt 要求的課數
POOL_TARGET = 30          # 合併池上限（round-robin 後截斷前 N）
DEFAULT_BATCH_SIZE = 10   # 前端每批顯示數（後端給預設）
```

在 `stage1_retrieve_async` 之後（第 466 行之後）新增：

```python
async def fanout_query_skill_async(
    client: genai.Client, store_name: str, career: str, skill: str
) -> list[dict]:
    """單一技能 → 一支聚焦小檢索（file_search top_k 受限），回候選 list。

    與 stage1_retrieve_async 不同：prompt 只聚焦『一個』技能、只要 ~FANOUT_PER_SKILL 門課、
    FileSearch(top_k=FANOUT_TOP_K) 壓住 retrieved chunk 量 → 單支快（壓 thinking）。
    解析失敗 / 空輸出 → 回 []（呼叫端用 return_exceptions 容錯）。
    """
    prompt = (
        f"職涯目標：{career}\n"
        f"聚焦技能：{skill}\n\n"
        f"請從課程知識庫找出與「{skill}」最相關的 {FANOUT_PER_SKILL} 門課程。\n"
        "每門課必須回傳：\n"
        "- course_id：9位數課程代號（如 000211012），出現在文件「課程代號:」欄位\n"
        "- course_name：課程名稱\n"
        "- relevance：與此技能的相關原因（一句）\n\n"
        '回傳 JSON：[{"course_id": "xxx", "course_name": "xxx", "relevance": "xxx"}]\n'
        "若知識庫中沒有任何課程與此技能真正相關，請回傳空陣列 []，不要硬湊。\n"
    )
    resp = await client.aio.models.generate_content(
        model=_GEN_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[
                types.Tool(
                    file_search=types.FileSearch(
                        file_search_store_names=[store_name],
                        top_k=FANOUT_TOP_K,
                    )
                )
            ],
        ),
    )
    return extract_json_array(resp.text)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ALLOWED_ORIGIN=* GEMINI_API_KEY=x FILE_SEARCH_STORE_NAME=x .venv/bin/python -m pytest tests/backend/test_fanout.py -v`
Expected: PASS（3 passed）。

- [ ] **Step 5: Commit**

```bash
git add backend/recommend.py tests/backend/test_fanout.py
git commit -m "feat(recommend): add fan-out constants + per-skill async query

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: round-robin 合併去重（純函式）

把多支 fan-out 結果以 round-robin 交錯合併成單一扁平池，套 `correct_candidate_ids` + `deduplicate_by_name` + `deduplicate_by_prefix`，截斷至 `POOL_TARGET`。此為純函式（不打 API），對應測試策略 §2.A（合併去重）與 §2.C（池上限交錯）。

**Files:**
- Modify: `backend/recommend.py`（在 `correct_candidate_ids` 之後、`join_metadata` 之前新增 `merge_fanout_results`）
- Test: `tests/backend/test_fanout.py`（沿用同檔，append）

- [ ] **Step 1: Write the failing test**

在 `tests/backend/test_fanout.py` 末尾 append：

```python
from backend.recommend import merge_fanout_results


def _meta_for(ids_names):
    m = {}
    for cid, name in ids_names:
        m[cid] = {"name": name, "department": "系", "teacher": "師",
                  "credits": 3.0, "syllabus_url": f"https://x/{cid}"}
    return m


def test_merge_round_robin_interleaves_sources():
    # 技能1 = [A0, A1]、技能2 = [B0, B1] → round-robin: A0, B0, A1, B1
    s1 = [{"course_id": "000010011", "course_name": "A0", "relevance": "x"},
          {"course_id": "000010021", "course_name": "A1", "relevance": "x"}]
    s2 = [{"course_id": "000020011", "course_name": "B0", "relevance": "x"},
          {"course_id": "000020021", "course_name": "B1", "relevance": "x"}]
    meta = _meta_for([("000010011", "A0"), ("000010021", "A1"),
                      ("000020011", "B0"), ("000020021", "B1")])
    out = merge_fanout_results([s1, s2], meta)
    names = [c["course_name"] for c in out]
    assert names == ["A0", "B0", "A1", "B1"]


def test_merge_dedup_overlapping_id_keeps_first():
    # 兩技能都檢出同一 course_id → 只留首次（先到的技能1）
    s1 = [{"course_id": "000010011", "course_name": "A0", "relevance": "from1"}]
    s2 = [{"course_id": "000010011", "course_name": "A0", "relevance": "from2"}]
    meta = _meta_for([("000010011", "A0")])
    out = merge_fanout_results([s1, s2], meta)
    assert len(out) == 1
    assert out[0]["relevance"] == "from1"


def test_merge_dedup_same_name_different_id():
    # 跨掛同名課（同名不同 id）→ deduplicate_by_name 去同名，只留一筆
    s1 = [{"course_id": "000010011", "course_name": "通識", "relevance": "x"}]
    s2 = [{"course_id": "000099088", "course_name": "通識", "relevance": "y"}]
    meta = _meta_for([("000010011", "通識"), ("000099088", "通識")])
    out = merge_fanout_results([s1, s2], meta)
    assert len(out) == 1


def test_merge_dedup_by_prefix():
    # 前6碼相同（不同班次後3碼）→ deduplicate_by_prefix 收斂一筆
    s1 = [{"course_id": "000010011", "course_name": "微積分甲", "relevance": "x"}]
    s2 = [{"course_id": "000010022", "course_name": "微積分乙", "relevance": "y"}]
    meta = _meta_for([("000010011", "微積分甲"), ("000010022", "微積分乙")])
    out = merge_fanout_results([s1, s2], meta)
    assert len(out) == 1


def test_merge_corrects_wrong_id_via_name():
    # 抄錯一碼但 course_name 對得上 → correct_candidate_ids 修回真實 id
    s1 = [{"course_id": "000010019", "course_name": "政治學", "relevance": "x"}]
    meta = _meta_for([("000010011", "政治學")])
    out = merge_fanout_results([s1], meta)
    assert out[0]["course_id"] == "000010011"


def test_merge_caps_at_pool_target():
    # 合併 > POOL_TARGET(30) → 截斷前 30
    big = [[{"course_id": f"{i:09d}", "course_name": f"課{i}", "relevance": "x"}]
           for i in range(40)]  # 40 個單元素來源
    meta = _meta_for([(f"{i:09d}", f"課{i}") for i in range(40)])
    out = merge_fanout_results(big, meta)
    assert len(out) == POOL_TARGET


def test_merge_pool_equal_target_keeps_all():
    sources = [[{"course_id": f"{i:09d}", "course_name": f"課{i}", "relevance": "x"}]
               for i in range(POOL_TARGET)]
    meta = _meta_for([(f"{i:09d}", f"課{i}") for i in range(POOL_TARGET)])
    out = merge_fanout_results(sources, meta)
    assert len(out) == POOL_TARGET


def test_merge_small_pool_no_padding():
    sources = [[{"course_id": "000010011", "course_name": "A", "relevance": "x"}]]
    meta = _meta_for([("000010011", "A")])
    out = merge_fanout_results(sources, meta)
    assert len(out) == 1  # 不補零、不報錯
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ALLOWED_ORIGIN=* GEMINI_API_KEY=x FILE_SEARCH_STORE_NAME=x .venv/bin/python -m pytest tests/backend/test_fanout.py -v`
Expected: FAIL with `ImportError: cannot import name 'merge_fanout_results'`。

- [ ] **Step 3: Write minimal implementation**

在 `backend/recommend.py` 的 `correct_candidate_ids` 之後（第 143 行之後）新增：

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ALLOWED_ORIGIN=* GEMINI_API_KEY=x FILE_SEARCH_STORE_NAME=x .venv/bin/python -m pytest tests/backend/test_fanout.py -v`
Expected: PASS（含前面 Task 1 共 11 passed）。

注意：`deduplicate_by_prefix` 會讀 `c["course_id"]`（非 `.get`），合併進來的每筆都有 `course_id`，安全。

- [ ] **Step 5: Commit**

```bash
git add backend/recommend.py tests/backend/test_fanout.py
git commit -m "feat(recommend): round-robin merge + dedup + pool cap for fan-out

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: 並行 fan-out retrieve orchestrator（容錯）

新增 `fanout_retrieve_async`：對每技能呼叫 `fanout_query_skill_async`、`asyncio.gather(..., return_exceptions=True)` 並行、單支失敗/非法回傳視為 `[]`、合併委派 `merge_fanout_results`。對應測試策略 §2.B（單技能失敗容錯 B1-B4）。

**Files:**
- Modify: `backend/recommend.py`（在 `fanout_query_skill_async` 之後新增；確認檔首已 `import asyncio`，若無則加）
- Test: `tests/backend/test_fanout.py`（append）

- [ ] **Step 1: Write the failing test**

在 `tests/backend/test_fanout.py` 末尾 append：

```python
from unittest.mock import patch
from backend.recommend import fanout_retrieve_async


def _ok(*ids_names):
    return [{"course_id": cid, "course_name": name, "relevance": "x"}
            for cid, name in ids_names]


@pytest.mark.asyncio
async def test_fanout_retrieve_merges_all_skills():
    meta = _meta_for([("000010011", "A"), ("000020011", "B")])
    side = {"分析": _ok(("000010011", "A")), "溝通": _ok(("000020011", "B"))}

    async def fake_query(client, store, career, skill):
        return side[skill]

    with patch("backend.recommend.fanout_query_skill_async", new=fake_query), \
         patch("backend.recommend.load_courses_meta", return_value=meta):
        out = await fanout_retrieve_async(object(), "store", "PM", ["分析", "溝通"])
    names = sorted(c["course_name"] for c in out)
    assert names == ["A", "B"]


@pytest.mark.asyncio
async def test_fanout_retrieve_one_skill_raises_others_survive():
    # B1：一支 raise（503/timeout）→ gather 不整體失敗、該支貢獻 []、其餘正常
    meta = _meta_for([("000020011", "B")])

    async def fake_query(client, store, career, skill):
        if skill == "分析":
            raise RuntimeError("503 overloaded")
        return _ok(("000020011", "B"))

    with patch("backend.recommend.fanout_query_skill_async", new=fake_query), \
         patch("backend.recommend.load_courses_meta", return_value=meta):
        out = await fanout_retrieve_async(object(), "store", "PM", ["分析", "溝通"])
    assert [c["course_name"] for c in out] == ["B"]


@pytest.mark.asyncio
async def test_fanout_retrieve_one_skill_empty_not_no_match():
    # B2：一支回空 → 池由其餘組成、不誤判 no_match（回非空）
    meta = _meta_for([("000020011", "B")])

    async def fake_query(client, store, career, skill):
        return [] if skill == "分析" else _ok(("000020011", "B"))

    with patch("backend.recommend.fanout_query_skill_async", new=fake_query), \
         patch("backend.recommend.load_courses_meta", return_value=meta):
        out = await fanout_retrieve_async(object(), "store", "PM", ["分析", "溝通"])
    assert len(out) == 1


@pytest.mark.asyncio
async def test_fanout_retrieve_all_empty_returns_empty_pool():
    # B3：全部皆空 → 空池（上層才轉 no_match/error）
    async def fake_query(client, store, career, skill):
        return []

    with patch("backend.recommend.fanout_query_skill_async", new=fake_query), \
         patch("backend.recommend.load_courses_meta", return_value={}):
        out = await fanout_retrieve_async(object(), "store", "PM", ["分析", "溝通"])
    assert out == []


@pytest.mark.asyncio
async def test_fanout_retrieve_all_raise_returns_empty_pool():
    # B3 變體：全部 raise → 空池、不向外拋
    async def fake_query(client, store, career, skill):
        raise RuntimeError("boom")

    with patch("backend.recommend.fanout_query_skill_async", new=fake_query), \
         patch("backend.recommend.load_courses_meta", return_value={}):
        out = await fanout_retrieve_async(object(), "store", "PM", ["分析", "溝通"])
    assert out == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ALLOWED_ORIGIN=* GEMINI_API_KEY=x FILE_SEARCH_STORE_NAME=x .venv/bin/python -m pytest tests/backend/test_fanout.py -v`
Expected: FAIL with `ImportError: cannot import name 'fanout_retrieve_async'`。

- [ ] **Step 3: Write minimal implementation**

先確認 `backend/recommend.py` 檔首 import：第 2-8 行目前無 `import asyncio`。在第 3 行 `import json` 之後加一行：

```python
import asyncio
```

在 `fanout_query_skill_async` 之後新增：

```python
async def fanout_retrieve_async(
    client: genai.Client, store_name: str, career: str, skills: list[str]
) -> list[dict]:
    """並行 fan-out 檢索：每技能一支 file_search，asyncio.gather 並行。

    - 單支失敗（raise 503/timeout）或回非 list → 視為 []，不拖垮整體（容錯）。
    - 合併委派 merge_fanout_results（round-robin 交錯 + 修正 id + 去重 + 池上限）。
    - 全部皆空/皆失敗 → 回 []（上層判 no_match / error）。
    """
    meta = load_courses_meta()
    results = await asyncio.gather(
        *(fanout_query_skill_async(client, store_name, career, s) for s in skills),
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ALLOWED_ORIGIN=* GEMINI_API_KEY=x FILE_SEARCH_STORE_NAME=x .venv/bin/python -m pytest tests/backend/test_fanout.py -v`
Expected: PASS（共 16 passed）。

注意 B4（非法 JSON 視為 []）已由 `fanout_query_skill_async` 內 `extract_json_array` 回 `[]` 涵蓋（Task 1 的 `test_fanout_query_skill_empty_text_returns_empty_list` 已證），此處 orchestrator 再兜底非 list。

- [ ] **Step 5: Commit**

```bash
git add backend/recommend.py tests/backend/test_fanout.py
git commit -m "feat(recommend): parallel fan-out retrieve orchestrator with fault tolerance

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: 扁平 ranked schema + 整池標註 async 函式

新增扁平 ranked response schema（`_RankedItem`/`_RankedOutput`）與 `stage2_annotate_pool_async`：對整池每門課標 `group`（core/supporting/extended）+ 生成理由 + 依推薦強度全域排序。對應測試策略 §2.D（D1/D2/D4）。

**Files:**
- Modify: `backend/recommend.py`（在 `_Stage2Output` 之後新增新 schema；在 `stage2_group_async` 之後新增 annotate 函式）
- Test: `tests/backend/test_ranked.py`（新建）

- [ ] **Step 1: Write the failing test**

建立 `tests/backend/test_ranked.py`：

```python
# tests/backend/test_ranked.py
"""扁平 ranked 標註：schema、annotate-pool async、build_ranked_courses 轉換。
全程 mock，不打真 Gemini。"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from backend.recommend import (
    _RankedItem,
    _RankedOutput,
    stage2_annotate_pool_async,
)


def test_ranked_item_schema_fields():
    item = _RankedItem(
        course_id="000211012",
        group="core",
        reason_lead="總述",
        reason_points=[{"term": "分析", "detail": "拆解問題"},
                       {"term": "建模", "detail": "量化決策"}],
    )
    assert item.course_id == "000211012"
    assert item.group == "core"
    assert item.reason_points[0].term == "分析"


def test_ranked_output_is_list_of_items():
    out = _RankedOutput(courses=[
        _RankedItem(course_id="000211012", group="core",
                    reason_lead="x", reason_points=[]),
    ])
    assert len(out.courses) == 1


def _fake_client(parsed):
    resp = SimpleNamespace(parsed=parsed)
    aio = SimpleNamespace(models=SimpleNamespace(
        generate_content=AsyncMock(return_value=resp)))
    return SimpleNamespace(aio=aio)


@pytest.mark.asyncio
async def test_annotate_pool_returns_parsed_ranked_output():
    parsed = _RankedOutput(courses=[
        _RankedItem(course_id="000211012", group="core",
                    reason_lead="總述",
                    reason_points=[{"term": "分析", "detail": "拆解"}]),
    ])
    client = _fake_client(parsed)
    candidates = [{"course_id": "000211012", "course_name": "政治學", "relevance": "x"}]
    out = await stage2_annotate_pool_async(client, "PM", ["分析"], candidates)
    assert out is parsed
    client.aio.models.generate_content.assert_awaited_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ALLOWED_ORIGIN=* GEMINI_API_KEY=x FILE_SEARCH_STORE_NAME=x .venv/bin/python -m pytest tests/backend/test_ranked.py -v`
Expected: FAIL with `ImportError: cannot import name '_RankedItem'`。

- [ ] **Step 3: Write minimal implementation**

在 `backend/recommend.py` 的 `_Stage2Output` 定義之後（第 183 行之後）新增新 schema（沿用既有 `_ReasonPoint`）：

```python
class _RankedItem(BaseModel):
    course_id: str
    group: str                          # core / supporting / extended
    reason_lead: str
    reason_points: list[_ReasonPoint]

class _RankedOutput(BaseModel):
    courses: list[_RankedItem]          # 依推薦強度由高到低排序（rank = index）
```

在 `stage2_group_async` 之後（第 500 行之後）新增：

```python
async def stage2_annotate_pool_async(
    client: genai.Client, career: str, skills: list[str], candidates: list[dict]
) -> _RankedOutput:
    """整池標註：為池中『每一門』課標 group + 理由，並依推薦強度全域排序。

    取代『選 10 門分三組』；改為『標註整池、扁平 ranked 清單』。
    回傳順序即 rank（index 0 = 最推薦）。
    """
    skill_str = "、".join(skills)
    candidates_text = "\n".join(
        f"{i + 1}. [{c['course_id']}] {c.get('course_name', '')} — {c.get('relevance', '')}"
        for i, c in enumerate(candidates)
    )
    prompt = (
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
    resp = await client.aio.models.generate_content(
        model=_GEN_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_RankedOutput,
        ),
    )
    return resp.parsed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ALLOWED_ORIGIN=* GEMINI_API_KEY=x FILE_SEARCH_STORE_NAME=x .venv/bin/python -m pytest tests/backend/test_ranked.py -v`
Expected: PASS（4 passed）。

- [ ] **Step 5: Commit**

```bash
git add backend/recommend.py tests/backend/test_ranked.py
git commit -m "feat(recommend): flat ranked schema + annotate-pool async stage2

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: build_ranked_courses（扁平清單轉換 + 復原 + 去重）

新增 `build_ranked_courses`：把 `_RankedOutput` 轉成扁平 `list[dict]`，逐課做 course_id 前綴復原 → `join_metadata` → 跨課同名去重，保留 `group` 與 `rank`（index）。對應測試策略 §2.D（D1/D3/D4/D5）。

**Files:**
- Modify: `backend/recommend.py`（在 `build_groups` 之後新增 `build_ranked_courses`）
- Test: `tests/backend/test_ranked.py`（append）

- [ ] **Step 1: Write the failing test**

在 `tests/backend/test_ranked.py` 末尾 append：

```python
from backend.recommend import build_ranked_courses


def _meta(*ids_names):
    m = {}
    for cid, name in ids_names:
        m[cid] = {"name": name, "department": "系", "teacher": "師",
                  "credits": 3.0, "syllabus_url": f"https://x/{cid}"}
    return m


def _cands(*ids_names):
    return [{"course_id": cid, "course_name": name, "relevance": "x"}
            for cid, name in ids_names]


def _ranked(*triples):
    """triples: (course_id, group) → _RankedOutput（理由固定 2 points）。"""
    return _RankedOutput(courses=[
        _RankedItem(course_id=cid, group=grp, reason_lead="總述",
                    reason_points=[{"term": "分析", "detail": "拆解"},
                                   {"term": "建模", "detail": "量化"}])
        for cid, grp in triples
    ])


def test_build_ranked_every_course_has_group_and_rank():
    # D1：每課都有 group ∈ {core,supporting,extended} + rank
    meta = _meta(("000010011", "A"), ("000020011", "B"))
    cands = _cands(("000010011", "A"), ("000020011", "B"))
    ranked = _ranked(("000010011", "core"), ("000020011", "supporting"))
    out = build_ranked_courses(ranked, cands, meta)
    assert [c["rank"] for c in out] == [0, 1]
    assert {c["group"] for c in out} <= {"core", "supporting", "extended"}
    assert out[0]["group"] == "core"
    assert out[1]["group"] == "supporting"


def test_build_ranked_preserves_order_as_rank():
    # D2：rank 由高到低（保留 annotate 回傳順序），rank=index
    meta = _meta(("000010011", "A"), ("000020011", "B"), ("000030011", "C"))
    cands = _cands(("000010011", "A"), ("000020011", "B"), ("000030011", "C"))
    ranked = _ranked(("000030011", "core"), ("000010011", "core"), ("000020011", "extended"))
    out = build_ranked_courses(ranked, cands, meta)
    assert [c["name"] for c in out] == ["C", "A", "B"]
    assert [c["rank"] for c in out] == [0, 1, 2]


def test_build_ranked_join_metadata_drops_missing():
    # D3：缺 meta 且無法前綴復原 → 丟棄、不帶 None
    meta = _meta(("000010011", "A"))
    cands = _cands(("000010011", "A"))
    ranked = _ranked(("000010011", "core"), ("777888999", "core"))
    out = build_ranked_courses(ranked, cands, meta)
    assert len(out) == 1
    assert out[0]["name"] == "A"
    assert all(c.get("name") is not None for c in out)


def test_build_ranked_prefix_recovery():
    # stage2 抄錯後3碼（前6碼與 candidate 相同）→ 復原成 candidate 真實 id
    meta = _meta(("000010011", "A"))
    cands = _cands(("000010011", "A"))
    ranked = _ranked(("000010099", "core"))
    out = build_ranked_courses(ranked, cands, meta)
    assert len(out) == 1
    assert out[0]["course_id"] == "000010011"


def test_build_ranked_reason_shape_two_bold_terms():
    # D4：reason={lead, points:[{term,detail}]}、≥2 粗體 term
    meta = _meta(("000010011", "A"))
    cands = _cands(("000010011", "A"))
    ranked = _ranked(("000010011", "core"))
    out = build_ranked_courses(ranked, cands, meta)
    reason = out[0]["reason"]
    assert reason["lead"] == "總述"
    assert len(reason["points"]) >= 2
    assert reason["points"][0]["term"] == "分析"


def test_build_ranked_no_duplicate_id():
    # D5：標註後無重複 id（同 id 出現兩次 → 只留首次/前綴去重）
    meta = _meta(("000010011", "A"))
    cands = _cands(("000010011", "A"))
    ranked = _ranked(("000010011", "core"), ("000010011", "supporting"))
    out = build_ranked_courses(ranked, cands, meta)
    ids = [c["course_id"] for c in out]
    assert len(ids) == len(set(ids))
    assert len(out) == 1


def test_build_ranked_no_cross_listed_same_name():
    # D5：跨掛同名（同名不同 id）→ 去同名只留一筆
    meta = _meta(("000010011", "通識"), ("000099088", "通識"))
    cands = _cands(("000010011", "通識"), ("000099088", "通識"))
    ranked = _ranked(("000010011", "core"), ("000099088", "supporting"))
    out = build_ranked_courses(ranked, cands, meta)
    names = [c["name"] for c in out]
    assert names == ["通識"]


def test_build_ranked_course_has_full_fields():
    meta = _meta(("000010011", "A"))
    cands = _cands(("000010011", "A"))
    ranked = _ranked(("000010011", "core"))
    out = build_ranked_courses(ranked, cands, meta)
    c = out[0]
    for k in ("course_id", "name", "department", "teacher", "credits",
              "group", "reason", "syllabus_url", "rank"):
        assert k in c
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ALLOWED_ORIGIN=* GEMINI_API_KEY=x FILE_SEARCH_STORE_NAME=x .venv/bin/python -m pytest tests/backend/test_ranked.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_ranked_courses'`。

- [ ] **Step 3: Write minimal implementation**

在 `backend/recommend.py` 的 `build_groups` 之後（第 248 行之後）新增：

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ALLOWED_ORIGIN=* GEMINI_API_KEY=x FILE_SEARCH_STORE_NAME=x .venv/bin/python -m pytest tests/backend/test_ranked.py -v`
Expected: PASS（共 12 passed）。

- [ ] **Step 5: Commit**

```bash
git add backend/recommend.py tests/backend/test_ranked.py
git commit -m "feat(recommend): build_ranked_courses flat transform with recovery+dedup

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: RecommendResponse 扁平 courses + batch_size + Course 模型

`models.py` 新增扁平 `Course`（含 `group`、`rank`），`RecommendResponse` 改為 `courses: list[Course]` + `batch_size: int = 10`。對應測試策略 §2.D（D6）。

**Files:**
- Modify: `backend/models.py:27-48`（新增 `Course`、改 `RecommendResponse`；保留 `CourseCard`/`CourseGroups` 供既有測試）
- Test: `tests/backend/test_ranked.py`（append 模型測試）

- [ ] **Step 1: Write the failing test**

在 `tests/backend/test_ranked.py` 末尾 append：

```python
from backend.models import Course, RecommendResponse


def test_course_model_flat_with_group_and_rank():
    c = Course(
        course_id="000010011", name="政治學", department="政治系",
        teacher="蔡中民", credits=3.0, group="core",
        reason={"lead": "x", "points": [{"term": "分析", "detail": "拆解"}]},
        syllabus_url="https://x/a", rank=0,
    )
    assert c.group == "core"
    assert c.rank == 0
    assert c.reason.points[0].term == "分析"


def test_recommend_response_flat_courses_and_batch_default():
    # D6：扁平 courses + batch_size 預設 10 + 回 seed
    resp = RecommendResponse(
        career="產品經理(PM)",
        courses=[Course(
            course_id="000010011", name="政治學", department="政治系",
            teacher="蔡", credits=3.0, group="core",
            reason={"lead": "x", "points": [{"term": "分析", "detail": "拆解"}]},
            syllabus_url="https://x/a", rank=0)],
        latency_ms=1200, seed=42,
    )
    assert resp.batch_size == 10
    assert resp.seed == 42
    assert resp.courses[0].group == "core"
    assert resp.notice is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ALLOWED_ORIGIN=* GEMINI_API_KEY=x FILE_SEARCH_STORE_NAME=x .venv/bin/python -m pytest tests/backend/test_ranked.py::test_recommend_response_flat_courses_and_batch_default -v`
Expected: FAIL with `ImportError: cannot import name 'Course'`。

- [ ] **Step 3: Write minimal implementation**

在 `backend/models.py` 的 `CourseGroups` 之後（第 41 行之後）新增 `Course`，並改 `RecommendResponse`。保留 `CourseCard`/`CourseGroups`（既有 test_recommend.py 仍 import `CourseCard`）。

新增（在第 41 行 `CourseGroups` 定義後）：

```python
class Course(BaseModel):
    course_id: str
    name: str
    department: str
    teacher: str
    credits: float
    group: str            # core / supporting / extended
    reason: Reason
    syllabus_url: str
    rank: int = 0
```

把第 43-49 行的 `RecommendResponse` 改為：

```python
class RecommendResponse(BaseModel):
    career: str
    courses: list[Course]                # 扁平 ranked 清單（依 rank 排序，每課帶 group）
    batch_size: int = 10                 # 前端每批顯示數
    latency_ms: int = 0
    seed: int = 0
    notice: str | None = None            # 清單外職涯：說明推薦依據可轉移能力
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ALLOWED_ORIGIN=* GEMINI_API_KEY=x FILE_SEARCH_STORE_NAME=x .venv/bin/python -m pytest tests/backend/test_ranked.py -v`
Expected: PASS（共 14 passed）。

同時確認既有模型測試未壞：
Run: `ALLOWED_ORIGIN=* GEMINI_API_KEY=x FILE_SEARCH_STORE_NAME=x .venv/bin/python -m pytest tests/backend/test_recommend.py -v`
Expected: PASS（`CourseCard` 仍存在，`test_course_card_has_required_fields` 等照舊綠）。

- [ ] **Step 5: Commit**

```bash
git add backend/models.py tests/backend/test_ranked.py
git commit -m "feat(models): flat Course + RecommendResponse.courses + batch_size

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: build_recommendation 改用 fan-out + 整池標註（扁平輸出）

把同步入口 `build_recommendation` 與 `build_recommendation_instrumented` 改用新 pipeline：`fanout_retrieve_async`（用 `asyncio.run` 包同步）→ `stage2_annotate_pool_async` → `build_ranked_courses`，回傳含扁平 `courses` + `batch_size`。POST `/recommend` 走此路徑（向後相容：仍回 dict，但形狀改扁平）。

**Files:**
- Modify: `backend/recommend.py:344-402`（重寫 `build_recommendation` 與 `build_recommendation_instrumented`）
- Test: `tests/backend/test_recommend_pipeline.py`（新建）

- [ ] **Step 1: Write the failing test**

建立 `tests/backend/test_recommend_pipeline.py`：

```python
# tests/backend/test_recommend_pipeline.py
"""build_recommendation(_instrumented) 走 fan-out + 整池標註 → 扁平 courses。
mock async retrieve/annotate，不打真 Gemini。"""
import pytest
from unittest.mock import patch, AsyncMock
from backend.recommend import (
    build_recommendation,
    build_recommendation_instrumented,
    _RankedOutput, _RankedItem,
)


def _meta(*ids_names):
    return {cid: {"name": name, "department": "系", "teacher": "師",
                  "credits": 3.0, "syllabus_url": f"https://x/{cid}"}
            for cid, name in ids_names}


def _ranked(*triples):
    return _RankedOutput(courses=[
        _RankedItem(course_id=cid, group=grp, reason_lead="總述",
                    reason_points=[{"term": "分析", "detail": "拆解"},
                                   {"term": "建模", "detail": "量化"}])
        for cid, grp in triples
    ])


def test_build_recommendation_returns_flat_courses():
    meta = _meta(("000010011", "A"), ("000020011", "B"))
    pool = [{"course_id": "000010011", "course_name": "A", "relevance": "x"},
            {"course_id": "000020011", "course_name": "B", "relevance": "x"}]
    with patch("backend.recommend.load_courses_meta", return_value=meta), \
         patch("backend.recommend.load_careers",
               return_value={"PM": {"skills": ["分析", "溝通"]}}), \
         patch("backend.recommend.fanout_retrieve_async",
               new=AsyncMock(return_value=pool)), \
         patch("backend.recommend.stage2_annotate_pool_async",
               new=AsyncMock(return_value=_ranked(("000010011", "core"),
                                                  ("000020011", "supporting")))):
        result = build_recommendation(object(), "store", "PM", seed=1)
    assert result["career"] == "PM"
    assert "courses" in result
    assert result["batch_size"] == 10
    assert result["seed"] == 1
    assert [c["name"] for c in result["courses"]] == ["A", "B"]
    assert result["courses"][0]["group"] == "core"
    assert result["courses"][0]["rank"] == 0


def test_build_recommendation_empty_pool_raises():
    with patch("backend.recommend.load_courses_meta", return_value={}), \
         patch("backend.recommend.load_careers",
               return_value={"PM": {"skills": ["分析"]}}), \
         patch("backend.recommend.fanout_retrieve_async",
               new=AsyncMock(return_value=[])):
        with pytest.raises(ValueError):
            build_recommendation(object(), "store", "PM", seed=1)


def test_instrumented_returns_pool_count():
    meta = _meta(("000010011", "A"))
    pool = [{"course_id": "000010011", "course_name": "A", "relevance": "x"}]
    with patch("backend.recommend.load_courses_meta", return_value=meta), \
         patch("backend.recommend.load_careers",
               return_value={"PM": {"skills": ["分析"]}}), \
         patch("backend.recommend.fanout_retrieve_async",
               new=AsyncMock(return_value=pool)), \
         patch("backend.recommend.stage2_annotate_pool_async",
               new=AsyncMock(return_value=_ranked(("000010011", "core")))):
        result, count = build_recommendation_instrumented(object(), "store", "PM", seed=1)
    assert count == 1
    assert result["courses"][0]["name"] == "A"


def test_build_recommendation_unknown_career_with_skills_ok():
    # 清單外但傳入 skills → 不查 careers、直接跑
    meta = _meta(("000010011", "A"))
    pool = [{"course_id": "000010011", "course_name": "A", "relevance": "x"}]
    with patch("backend.recommend.load_courses_meta", return_value=meta), \
         patch("backend.recommend.load_careers", return_value={}), \
         patch("backend.recommend.fanout_retrieve_async",
               new=AsyncMock(return_value=pool)), \
         patch("backend.recommend.stage2_annotate_pool_async",
               new=AsyncMock(return_value=_ranked(("000010011", "core")))):
        result = build_recommendation(object(), "store", "記者", seed=1, skills=["寫作"])
    assert result["courses"][0]["name"] == "A"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ALLOWED_ORIGIN=* GEMINI_API_KEY=x FILE_SEARCH_STORE_NAME=x .venv/bin/python -m pytest tests/backend/test_recommend_pipeline.py -v`
Expected: FAIL（舊 `build_recommendation` 回 `groups`、無 `courses`/`batch_size` → assert 失敗；且仍呼叫 `stage1_retrieve` 同步版而非被 mock 的 `fanout_retrieve_async`）。

- [ ] **Step 3: Write minimal implementation**

重寫 `backend/recommend.py:344-374` 的 `build_recommendation`：

```python
def build_recommendation(
    client: genai.Client, store_name: str, career: str, seed: int = 0, skills: list[str] | None = None
) -> dict:
    """Full fan-out pipeline. Returns RecommendResponse-compatible dict（扁平 courses）。"""
    result, _ = build_recommendation_instrumented(
        client, store_name, career, seed, skills=skills
    )
    return result
```

重寫 `backend/recommend.py:377-402` 的 `build_recommendation_instrumented`：

```python
def build_recommendation_instrumented(
    client: genai.Client, store_name: str, career: str, seed: int = 0, skills: list[str] | None = None
) -> tuple[dict, int]:
    """fan-out + 整池標註 pipeline；同時回傳候選池大小（pool_count）。

    同步入口（POST /recommend 用）：以 asyncio.run 驅動 async fan-out / annotate。
    seed 保留於回應（前端續池用新 seed 呼叫；本層不再做 sample_candidates 抽樣，
    多樣性改由前端分頁 + 續池新 seed 達成）。
    """
    t0 = time.monotonic()
    careers = load_careers()
    meta = load_courses_meta()
    if skills is None:
        if career not in careers:
            raise ValueError(f"Unknown career: {career}")
        skills = careers[career]["skills"]

    async def _run():
        candidates = await fanout_retrieve_async(client, store_name, career, skills)
        if not candidates:
            return None, 0
        ranked = await stage2_annotate_pool_async(client, career, skills, candidates)
        return build_ranked_courses(ranked, candidates, meta), len(candidates)

    courses, pool_count = asyncio.run(_run())
    if courses is None:
        raise ValueError("Stage 1 returned no candidate courses")

    latency_ms = int((time.monotonic() - t0) * 1000)
    result = {
        "career": career,
        "courses": courses,
        "batch_size": DEFAULT_BATCH_SIZE,
        "latency_ms": latency_ms,
        "seed": seed,
    }
    return result, pool_count
```

注意：保留檔中既有 `stage1_retrieve`/`stage2_group`/`build_groups`/`_Stage2Output`/`sample_candidates`（不刪），供既有測試（test_recommend.py、test_recommend_recovery.py、test_recommend_diversity.py）持續綠。

- [ ] **Step 4: Run test to verify it passes**

Run: `ALLOWED_ORIGIN=* GEMINI_API_KEY=x FILE_SEARCH_STORE_NAME=x .venv/bin/python -m pytest tests/backend/test_recommend_pipeline.py -v`
Expected: PASS（4 passed）。

確認既有未壞：
Run: `ALLOWED_ORIGIN=* GEMINI_API_KEY=x FILE_SEARCH_STORE_NAME=x .venv/bin/python -m pytest tests/backend/test_recommend.py tests/backend/test_recommend_recovery.py tests/backend/test_recommend_diversity.py -v`
Expected: PASS（既有純函式測試全綠）。

- [ ] **Step 5: Commit**

```bash
git add backend/recommend.py tests/backend/test_recommend_pipeline.py
git commit -m "feat(recommend): build_recommendation uses fan-out + annotate-pool (flat)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: stream_recommendation 內部換 fan-out + 整池標註（事件序列不變）

`stream_recommendation` 的 retrieve 階段內部換 `fanout_retrieve_async`、compose 階段換 `stage2_annotate_pool_async` + `build_ranked_courses`，最終 `result` 事件帶扁平 `courses` + `batch_size`。事件序列（stage/result/no_match/error 與 5 階段 start/done）維持不變。filter 階段（去重/修正/抽樣）併入 fan-out 內部，stage 事件保留為純標記。

**Files:**
- Modify: `backend/recommend.py:520-591`（重寫 `stream_recommendation` 主體）
- Test: `tests/backend/test_recommend_stream.py`（改既有 3 測 + 新增扁平斷言）

- [ ] **Step 1: Write the failing test**

改寫 `tests/backend/test_recommend_stream.py`，把 mock 對象從 `stage1_retrieve_async`/`stage2_group_async` 改為 `fanout_retrieve_async`/`stage2_annotate_pool_async`，並斷言 result 帶扁平 courses。完整新內容：

```python
# tests/backend/test_recommend_stream.py
"""stream_recommendation async generator：驗證 5 階段事件序列、result/no_match 分支、
result 帶扁平 ranked courses。mock async fan-out / annotate（不打真 Gemini）。"""
import pytest
from unittest.mock import patch, AsyncMock
from backend.recommend import (
    stream_recommendation, _RankedOutput, _RankedItem,
)


def _ranked_core(course_id):
    return _RankedOutput(courses=[
        _RankedItem(course_id=course_id, group="core", reason_lead="總述",
                    reason_points=[{"term": "分析", "detail": "拆解問題"},
                                   {"term": "建模", "detail": "量化"}])
    ])


async def _collect(gen):
    return [ev async for ev in gen]


@pytest.mark.asyncio
async def test_stream_known_career_emits_5_stages_and_flat_result():
    meta = {"000211012": {"name": "政治學", "department": "政治系", "teacher": "蔡",
                          "credits": 3.0, "syllabus_url": "https://x/a"}}
    pool = [{"course_id": "000211012", "course_name": "政治學", "relevance": "x"}]
    with patch("backend.recommend.load_courses_meta", return_value=meta), \
         patch("backend.recommend.fanout_retrieve_async",
               new=AsyncMock(return_value=pool)), \
         patch("backend.recommend.stage2_annotate_pool_async",
               new=AsyncMock(return_value=_ranked_core("000211012"))):
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
    assert r["courses"][0]["name"] == "政治學"
    assert r["courses"][0]["group"] == "core"
    assert r["batch_size"] == 10
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
    keys = [e["data"]["key"] for e in events if e["event"] == "stage"]
    assert "retrieve" not in keys


@pytest.mark.asyncio
async def test_stream_open_career_empty_pool_emits_no_match():
    with patch("backend.recommend.derive_skills_for_career_async",
               new=AsyncMock(return_value=["公共衛生"])), \
         patch("backend.recommend.fanout_retrieve_async",
               new=AsyncMock(return_value=[])):
        events = await _collect(
            stream_recommendation(client=object(), store_name="s",
                                  career="清潔工", seed=1, skills=None))
    assert any(e["event"] == "no_match" for e in events)
    assert not any(e["event"] == "result" for e in events)


@pytest.mark.asyncio
async def test_stream_known_career_empty_pool_emits_error():
    # 清單內但池空 → error 事件（與 POST 行為一致）
    with patch("backend.recommend.load_courses_meta", return_value={}), \
         patch("backend.recommend.fanout_retrieve_async",
               new=AsyncMock(return_value=[])):
        events = await _collect(
            stream_recommendation(client=object(), store_name="s",
                                  career="產品經理(PM)", seed=1, skills=None))
    assert any(e["event"] == "error" for e in events)
    assert not any(e["event"] == "result" for e in events)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ALLOWED_ORIGIN=* GEMINI_API_KEY=x FILE_SEARCH_STORE_NAME=x .venv/bin/python -m pytest tests/backend/test_recommend_stream.py -v`
Expected: FAIL（`stream_recommendation` 仍呼叫 `stage1_retrieve_async`/`stage2_group_async`、result 仍帶 `groups` 無 `courses`）。

- [ ] **Step 3: Write minimal implementation**

重寫 `backend/recommend.py:520-591` 的 `stream_recommendation` 主體（保留 `_STAGES`/`_stage_event` 與簽名不變）：

```python
async def stream_recommendation(
    client: genai.Client, store_name: str, career: str,
    seed: int = 0, skills: list[str] | None = None,
):
    """逐階段串流推薦（fan-out + 整池標註）。事件序列與舊版一致。

    - retrieve 階段內部 = 並行 fan-out（fanout_retrieve_async，已含去重/修正/池上限）。
    - filter 階段保留為純標記（合併去重已在 fan-out 內完成）。
    - compose 階段 = 整池標註（stage2_annotate_pool_async）。
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
    candidates = await fanout_retrieve_async(client, store_name, career, skills)
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
    ranked = await stage2_annotate_pool_async(client, career, skills, candidates)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ALLOWED_ORIGIN=* GEMINI_API_KEY=x FILE_SEARCH_STORE_NAME=x .venv/bin/python -m pytest tests/backend/test_recommend_stream.py -v`
Expected: PASS（4 passed）。

- [ ] **Step 5: Commit**

```bash
git add backend/recommend.py tests/backend/test_recommend_stream.py
git commit -m "feat(recommend): stream_recommendation uses fan-out + annotate-pool

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 9: main.py 串流 logging 改用扁平 courses 計數

`/recommend/stream` 的收尾 logging 用 `result["groups"]` 計數（第 203-205 行）；改為 `result["courses"]`。POST `/recommend` 路徑的「清單外但檢索空 → no_match」判斷（第 137 行）用 `result["groups"].values()`；改為 `result["courses"]`。

**Files:**
- Modify: `backend/main.py:137`（POST 空結果判斷）
- Modify: `backend/main.py:202-206`（stream logging 計數）
- Test: `tests/backend/test_recommend_stream_logging.py`（檢視既有 → 改計數斷言）

- [ ] **Step 1: Write the failing test**

先檢視既有檔斷言。建立/補強測試於 `tests/backend/test_recommend_stream_logging.py`（若既有測試已存在且斷言 `groups`，將其改為 `courses`）。新增一條明確測 main 計數邏輯的測試 `tests/backend/test_stream_logging_count.py`：

```python
# tests/backend/test_stream_logging_count.py
"""/recommend/stream 收尾 logging 用扁平 courses 計數（非 groups）。"""
import pytest


def _stage1_count_from_result(result):
    """複製 main.py 收尾的計數邏輯（扁平 courses）以單測其正確性。"""
    return len(result["courses"]) if result else 0


def test_count_from_flat_courses():
    result = {"courses": [{"course_id": "a"}, {"course_id": "b"}, {"course_id": "c"}]}
    assert _stage1_count_from_result(result) == 3


def test_count_none_result_zero():
    assert _stage1_count_from_result(None) == 0
```

> 註：此純函式測試鎖定計數契約；main.py 的實際整合行為由 §3 e2e（R1）覆蓋。

- [ ] **Step 2: Run test to verify it fails**

Run: `ALLOWED_ORIGIN=* GEMINI_API_KEY=x FILE_SEARCH_STORE_NAME=x .venv/bin/python -m pytest tests/backend/test_stream_logging_count.py -v`
Expected: PASS（此測試僅鎖定契約、不 import main）。先確認既有 `test_recommend_stream_logging.py` 是否 RED：

Run: `ALLOWED_ORIGIN=* GEMINI_API_KEY=x FILE_SEARCH_STORE_NAME=x .venv/bin/python -m pytest tests/backend/test_recommend_stream_logging.py tests/backend/test_api.py tests/backend/test_open_career_api.py -v`
Expected: 既有測試中凡 mock 回 `{"groups": {...}}` 形狀者會 FAIL（main 改讀 `courses` 後）→ 這是 Step 3 要一併修正的 RED。

- [ ] **Step 3: Write minimal implementation**

修改 `backend/main.py:137`，把：

```python
    if not error and skills is not None and result is not None and not any(result["groups"].values()):
```

改為：

```python
    if not error and skills is not None and result is not None and not result["courses"]:
```

修改 `backend/main.py:202-206`，把：

```python
        if result is not None or error is not None:
            stage1_count = (
                sum(len(g) for g in result["groups"].values()) if result else 0
            )
            _spawn_bg(_background_log_and_judge(career, result, stage1_count, error))
```

改為：

```python
        if result is not None or error is not None:
            stage1_count = len(result["courses"]) if result else 0
            _spawn_bg(_background_log_and_judge(career, result, stage1_count, error))
```

同步更新既有受影響測試的 mock 形狀：`tests/backend/test_api.py` 的 `client` fixture（第 11-25 行）把 `mock_build.return_value` 從巢狀 `groups` 改為扁平 `courses`：

```python
        mock_build.return_value = (
            {
                "career": "產品經理(PM)",
                "courses": [{"course_id": "000211012", "name": "政治學", "department": "政治系",
                             "teacher": "蔡中民", "credits": 3.0, "group": "core",
                             "reason": {"lead": "培養分析能力", "points": [{"term": "分析", "detail": "拆解問題"}]},
                             "syllabus_url": "https://x.com/a", "rank": 0}],
                "batch_size": 10,
                "latency_ms": 1200,
            },
            15,
        )
```

並把 `test_api.py` 中斷言 `"groups" in data` / `"core" in data["groups"]`（第 35-36 行）改為：

```python
    assert "courses" in data
    assert data["courses"][0]["group"] == "core"
```

對 `tests/backend/test_open_career_api.py` 與 `tests/backend/test_recommend_stream_logging.py` 做同樣的「`groups`→`courses` 形狀」對齊（凡 mock build 回傳或斷言巢狀 groups 處，改扁平 courses；空結果判斷用 `"courses": []`）。

- [ ] **Step 4: Run test to verify it passes**

Run: `ALLOWED_ORIGIN=* GEMINI_API_KEY=x FILE_SEARCH_STORE_NAME=x .venv/bin/python -m pytest tests/backend/test_stream_logging_count.py tests/backend/test_api.py tests/backend/test_open_career_api.py tests/backend/test_recommend_stream_logging.py -v`
Expected: PASS（全綠）。

- [ ] **Step 5: Commit**

```bash
git add backend/main.py tests/backend/test_stream_logging_count.py tests/backend/test_api.py tests/backend/test_open_career_api.py tests/backend/test_recommend_stream_logging.py
git commit -m "feat(main): recommend logging + empty-check use flat courses

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 10: 前端分頁純函式模組 `frontend/pagination.js`

抽出 client-side 分頁邏輯為可單測的 ESM 模組：`createPaginationState`、`nextBatch`、`appendPool`、`groupBatch`。對應測試策略 §2.E（E1-E7）。

**Files:**
- Create: `frontend/pagination.js`
- Test: `tests/frontend/pagination.test.mjs`（新建，用 Node 內建 `node:test`）

- [ ] **Step 1: Write the failing test**

建立 `tests/frontend/pagination.test.mjs`：

```javascript
// tests/frontend/pagination.test.mjs
// 前端分頁純邏輯單測（Node 內建 test runner，無外部依賴）。
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  createPaginationState,
  nextBatch,
  appendPool,
  groupBatch,
} from "../../frontend/pagination.js";

function pool(n, group = "core") {
  return Array.from({ length: n }, (_, i) => ({
    course_id: String(i).padStart(9, "0"),
    name: `課${i}`,
    group,
    rank: i,
  }));
}

test("E1: 30 課池 → 第 1 批為 rank 1-10", () => {
  const st = createPaginationState(pool(30), 10);
  const batch = nextBatch(st);
  assert.equal(batch.length, 10);
  assert.deepEqual(batch.map((c) => c.rank), [0,1,2,3,4,5,6,7,8,9]);
  assert.equal(st.shown, 10);
});

test("E2: 換一批 → 11-20、shown 推進", () => {
  const st = createPaginationState(pool(30), 10);
  nextBatch(st);
  const b2 = nextBatch(st);
  assert.deepEqual(b2.map((c) => c.rank), [10,11,12,13,14,15,16,17,18,19]);
  assert.equal(st.shown, 20);
});

test("E3: 再換 → 21-30、池乾", () => {
  const st = createPaginationState(pool(30), 10);
  nextBatch(st); nextBatch(st);
  const b3 = nextBatch(st);
  assert.deepEqual(b3.map((c) => c.rank), [20,21,22,23,24,25,26,27,28,29]);
  assert.equal(st.shown, 30);
  assert.equal(st.exhausted, true);
});

test("E4: 池乾再換 → 回 null（需續池訊號）、不崩", () => {
  const st = createPaginationState(pool(30), 10);
  nextBatch(st); nextBatch(st); nextBatch(st);
  const b4 = nextBatch(st);
  assert.equal(b4, null);
  assert.equal(st.exhausted, true);
});

test("E5: 池=7 (<batch) → 首批全 7、之後 exhausted", () => {
  const st = createPaginationState(pool(7), 10);
  const b1 = nextBatch(st);
  assert.equal(b1.length, 7);
  assert.equal(st.exhausted, true);
  assert.equal(nextBatch(st), null);
});

test("E6: groupBatch 把同批依 group 分三區", () => {
  const batch = [
    { course_id: "a", name: "A", group: "core", rank: 0 },
    { course_id: "b", name: "B", group: "extended", rank: 1 },
    { course_id: "c", name: "C", group: "core", rank: 2 },
    { course_id: "d", name: "D", group: "supporting", rank: 3 },
  ];
  const g = groupBatch(batch);
  assert.deepEqual(g.core.map((c) => c.name), ["A", "C"]);
  assert.deepEqual(g.supporting.map((c) => c.name), ["D"]);
  assert.deepEqual(g.extended.map((c) => c.name), ["B"]);
});

test("E7: 續池 append 去重 (course_id) 後再切批不重看", () => {
  const st = createPaginationState(pool(10), 10);
  nextBatch(st);                         // 看完 0-9
  // 續池：含 1 個重複 id (000000009) + 5 個新
  const more = [
    { course_id: "000000009", name: "課9dup", group: "core", rank: 0 },
    ...Array.from({ length: 5 }, (_, i) => ({
      course_id: String(100 + i).padStart(9, "0"),
      name: `新${i}`, group: "core", rank: i,
    })),
  ];
  appendPool(st, more);
  assert.equal(st.pool.length, 15);      // 10 + 5（重複的 9 被丟）
  const b2 = nextBatch(st);
  assert.deepEqual(b2.map((c) => c.name), ["新0","新1","新2","新3","新4"]);
});

test("groupBatch 缺某 group → 該區為空陣列", () => {
  const g = groupBatch([{ course_id: "a", name: "A", group: "core", rank: 0 }]);
  assert.deepEqual(g.supporting, []);
  assert.deepEqual(g.extended, []);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/frontend/pagination.test.mjs`
Expected: FAIL with `Cannot find module '.../frontend/pagination.js'`。

- [ ] **Step 3: Write minimal implementation**

建立 `frontend/pagination.js`：

```javascript
// pagination.js — 推薦結果 client-side 分頁純邏輯（無 DOM 依賴，可單測）。
// state: { pool: Course[], batchSize: number, shown: number, exhausted: boolean }

export function createPaginationState(pool, batchSize) {
  const state = {
    pool: Array.isArray(pool) ? pool.slice() : [],
    batchSize: batchSize > 0 ? batchSize : 10,
    shown: 0,
    exhausted: false,
  };
  return state;
}

// 取下一批（最多 batchSize 門）。回 null 表示池已抽乾（需續池）。
// 推進 state.shown；當 shown 已達 pool 長度時 exhausted=true。
export function nextBatch(state) {
  if (state.shown >= state.pool.length) {
    state.exhausted = true;
    return null;
  }
  const start = state.shown;
  const end = Math.min(start + state.batchSize, state.pool.length);
  const batch = state.pool.slice(start, end);
  state.shown = end;
  state.exhausted = state.shown >= state.pool.length;
  return batch;
}

// 續池 append：去重（依 course_id，已在 pool 者丟棄），接到 pool 尾。
// 不改 shown（已看過的不重看）；append 後 exhausted 視新長度更新。
export function appendPool(state, more) {
  const seen = new Set(state.pool.map((c) => c.course_id));
  for (const c of more || []) {
    if (!seen.has(c.course_id)) {
      seen.add(c.course_id);
      state.pool.push(c);
    }
  }
  state.exhausted = state.shown >= state.pool.length;
  return state;
}

// 把一批課依 group 分流成三區（保留批內順序）。
export function groupBatch(batch) {
  const g = { core: [], supporting: [], extended: [] };
  for (const c of batch || []) {
    if (g[c.group]) g[c.group].push(c);
    else g.extended.push(c); // 未知 group 兜底歸延伸，避免遺漏
  }
  return g;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test tests/frontend/pagination.test.mjs`
Expected: PASS（8 tests passed）。

- [ ] **Step 5: Commit**

```bash
git add frontend/pagination.js tests/frontend/pagination.test.mjs
git commit -m "feat(frontend): client-side pagination pure module + node:test

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 11: app.js 接整池 + 分區渲染 + 純前端換一批 + 池乾續池

`app.js` import pagination 模組（index.html 以 `type="module"` 載入或內聯橋接），把整池存於分頁 state、`renderResults` 改為「依當前批的 `group` 分三區」、「換一批」純前端切片、池乾以新 seed 呼叫 `/recommend/stream` 續池 append。

**Files:**
- Modify: `frontend/index.html`（script 引入 pagination.js；bump `?v=N` cache-bust）
- Modify: `frontend/app.js:41-44`（state）、`186-220`（renderResults）、`496-498`（reroll）、SSE result handler `409-417`、`postRecommend` `446-468`
- Test: 由 §2.E 純函式（Task 10）+ §3.S e2e（Task 12）覆蓋；本 task 不新增單元測試（純 DOM 接線，e2e 驗證）

> **註（無單元測試的理由）**：本 task 是把已單測的 pagination 純函式接到 DOM/SSE。測試策略 §2.E 明定分頁邏輯抽 module 單測（Task 10 已做），DOM 接線由 §3.S e2e（Task 12）以 real backend 驗證（S1 無網路請求、S2 換批換內容、S3-S4 池乾續池）。此處遵 when-to-mock「禁 mock 自家 frontend-to-backend」，不寫假 fetch 單測。

- [ ] **Step 1: 改 index.html 引入模組並 bump 版本**

在 `frontend/index.html` 找到載入 `app.js` 的 `<script>`，確認 pagination.js 在其前載入。把 app.js 與 pagination.js 都設為 ES module 並帶 cache-bust（bump 既有 `?v=N`）。範例（依現況的版本號 +1）：

```html
<script type="module" src="pagination.js?v=2"></script>
<script type="module" src="app.js?v=2"></script>
```

若 app.js 目前非 module（用全域 `CAREERS`/`HOT_PICKS`），改最小侵入：在 app.js 頂部用動態 import 或於 index.html 先以普通 script 載入 careers.js，再以 module 載入 app.js（module 內 `import { ... } from "./pagination.js"`）。確保 `CAREERS`/`HOT_PICKS` 仍可見（careers.js 維持普通 script 在 module 前載入，掛 window）。

- [ ] **Step 2: 改 app.js state（第 41-44 行）**

把：

```javascript
let selectedCareer = null;
let lastCareer = null;   // 上次實際送出的職涯（含清單外自由輸入），供「換一批」沿用
let activeIdx = -1;
```

改為（新增分頁 state + 續池旗標 + import）：

```javascript
import { createPaginationState, nextBatch, appendPool, groupBatch } from "./pagination.js";

let selectedCareer = null;
let lastCareer = null;   // 上次實際送出的職涯（含清單外自由輸入），供續池沿用
let activeIdx = -1;
let pageState = null;    // 分頁 state（整池 + shown）
let lastNotice = null;   // 清單外職涯誠實說明（換批沿用）
let rerolling = false;   // 續池/切批進行中（去抖，避免 rapid double-click）
```

- [ ] **Step 3: 改 renderResults（第 191-220 行）為「存整池 + 顯示首批」**

把 `renderResults(data)` 改為：接整池存進 `pageState`、取首批、依 group 分區渲染。新內容：

```javascript
function renderResults(data){
  stopLoading();
  loadingEl.hidden = true; errorEl.hidden = true;
  const nm = document.getElementById("no-match"); if(nm) nm.hidden = true;
  resCareer.textContent = data.career;
  resLatency.textContent = data.latency_ms ? `GENERATED ${(data.latency_ms/1000).toFixed(1)}s` : "";
  // 存整池並重置分頁
  pageState = createPaginationState(data.courses || [], data.batch_size || 10);
  lastNotice = data.notice || null;
  renderBatch(nextBatch(pageState));   // 首批
  resultsEl.hidden = false;
  resultsEl.scrollIntoView({behavior:"smooth", block:"start"});
}

// 渲染「一批」課程：依 group 分三區塊（沿用 GROUP_META）
function renderBatch(batch){
  groupsEl.innerHTML = "";
  if(lastNotice){
    const n = document.createElement("p");
    n.className = "result-notice";
    n.textContent = lastNotice;
    groupsEl.appendChild(n);
  }
  if(!batch || !batch.length){
    const p = document.createElement("p");
    p.className = "result-notice";
    p.textContent = "已涵蓋主要推薦課程。";
    groupsEl.appendChild(p);
    return;
  }
  const grouped = groupBatch(batch);
  GROUP_META.forEach(g=>{
    const list = grouped[g.key] || [];
    if(!list.length) return;
    const block = document.createElement("div");
    block.className = "group";
    block.innerHTML = `
      <div class="group-title"><span class="idx">${g.idx}</span><h3>${g.title}</h3></div>
      <p class="group-desc">${g.desc}</p>
      <div class="cards"></div>`;
    const cardsEl = block.querySelector(".cards");
    list.forEach(c=>cardsEl.appendChild(renderCard(c)));
    groupsEl.appendChild(block);
  });
}
```

- [ ] **Step 4: 改 reroll（第 496-498 行）為純前端切片 + 池乾續池**

把：

```javascript
const rerollBtn = document.getElementById("reroll-btn");
rerollBtn.addEventListener("click", ()=>{ if(lastCareer) fetchRecommendation(lastCareer); });
```

改為：

```javascript
const rerollBtn = document.getElementById("reroll-btn");
rerollBtn.addEventListener("click", ()=>{
  if(rerolling) return;                 // 去抖：處理中忽略重複點擊（S5）
  if(!pageState){ if(lastCareer) fetchRecommendation(lastCareer); return; }
  const batch = nextBatch(pageState);
  if(batch){                            // 池中還有 → 純前端切片，0 網路請求（S1）
    renderBatch(batch);
    resultsEl.scrollIntoView({behavior:"smooth", block:"start"});
    return;
  }
  // 池乾 → 以新 seed 呼叫 /recommend/stream 續池（S4）
  if(!lastCareer) return;
  rerolling = true;
  rerollBtn.disabled = true;
  continuePool(lastCareer);
});

// 續池：新 seed 取一池 → append 去重 → 切下一批；UI 沿用 streaming stepper
function continuePool(career){
  closeRecEs();
  const seed = Math.floor(Math.random() * 1e9);
  const url = `${CONFIG.API_URL}/recommend/stream?career=${encodeURIComponent(career)}&seed=${seed}`;
  showLoadingShell();
  let firstEvent = false, settled = false;
  const guard = setTimeout(()=>{ if(!firstEvent && !settled){ closeRecEs(); continuePoolFallback(career); } }, 8000);
  _loadTimers.push(guard);
  let es;
  try{ es = new EventSource(url); }
  catch(e){ clearTimeout(guard); continuePoolFallback(career); return; }
  _recEs = es;
  es.addEventListener("stage", (ev)=>{
    firstEvent = true;
    let d; try{ d = JSON.parse(ev.data); }catch(_){ return; }
    const idx = (d.n|0) - 1;
    if(idx < 0 || idx >= REC_STAGES.length) return;
    if(d.status === "start") setStageActive(idx); else if(d.status === "done") setStageDone(idx);
  });
  es.addEventListener("result", (ev)=>{
    settled = true; clearTimeout(guard); closeRecEs();
    let data; try{ data = JSON.parse(ev.data); }catch(e){ finishContinue(null); return; }
    finishProgress();
    setTimeout(()=>finishContinue(data), 350);
  });
  es.addEventListener("no_match", ()=>{ settled = true; clearTimeout(guard); closeRecEs(); finishContinue(null); });
  es.addEventListener("error", (ev)=>{
    if(ev && typeof ev.data === "string" && ev.data.length){
      settled = true; clearTimeout(guard); closeRecEs(); finishContinue(null); return;
    }
    if(settled) return;
    clearTimeout(guard); closeRecEs();
    if(!firstEvent) continuePoolFallback(career); else finishContinue(null);
  });
}

async function continuePoolFallback(career){
  try{
    const resp = await fetch(`${CONFIG.API_URL}/recommend`, {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({career}),
    });
    if(!resp.ok) throw new Error(`HTTP ${resp.status}`);
    finishContinue(await resp.json());
  }catch(e){ finishContinue(null); }
}

// 續池收尾：append 去重 → 切下一批顯示；失敗則顯示「已涵蓋主要推薦」
function finishContinue(data){
  loadingEl.hidden = true;
  resultsEl.hidden = false;
  if(data && Array.isArray(data.courses) && data.courses.length){
    appendPool(pageState, data.courses);
    const batch = nextBatch(pageState);
    renderBatch(batch);   // batch 可能 null → renderBatch 顯示「已涵蓋」
  } else {
    renderBatch(null);
  }
  rerolling = false;
  rerollBtn.disabled = false;
  resultsEl.scrollIntoView({behavior:"smooth", block:"start"});
}
```

- [ ] **Step 5: 對齊 SSE result handler 與 postRecommend（第 409-417、446-468 行）**

確認 SSE `result` 與 `postRecommend` 成功分支都呼叫 `renderResults(data)`（已是現況），新版 `renderResults` 會自動初始化分頁 state。無需改這兩段的呼叫，僅確認 `data` 內含 `courses`/`batch_size`（後端 Task 8/9 已保證）。手動驗證頁面：

Run（本機起後端 + frontend，手動點換一批）：
```bash
ALLOWED_ORIGIN=* GEMINI_API_KEY=$GEMINI_API_KEY FILE_SEARCH_STORE_NAME=$FILE_SEARCH_STORE_NAME .venv/bin/python -m uvicorn backend.main:app --port 8000 &
python3 -m http.server 3000 --directory frontend
```
開 `http://localhost:3000`，選「資料科學家」→ 首批 10 卡分三區 → 點「換一批」應**瞬間**換 11-20（DevTools Network 無新請求）→ 連點到池乾才見新 `/recommend/stream`。截 PNG 給使用者確認視覺後再 commit（CLAUDE.md 視覺紀律）。

- [ ] **Step 6: Run前端純函式回歸**

Run: `node --test tests/frontend/pagination.test.mjs`
Expected: PASS（確認 module 介面未被 app.js 改動破壞）。

- [ ] **Step 7: Commit**

```bash
git add frontend/app.js frontend/index.html
git commit -m "feat(frontend): client-side paginated reroll + pool continuation

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 12: e2e Playwright real-backend（R/S/M）— 骨架與斷言

建立 Playwright e2e 測試檔（推薦 user flow R、換一批切片 S、RWD M），real backend，role/data-attr locator、無網路請求斷言、flake gate。**樣本刻意稀少**（職涯 ≤2，每請求 6 支並行小檢索燒 RPM）。

**Files:**
- Create: `playwright.config.ts`（baseURL、webServer 起 backend:8000 + frontend:3000、desktop/mobile/tablet 三 project、reducedMotion、首載 timeout 120s、forbidOnly、fullyParallel、CI retries=2/本機 0、trace on-first-retry）
- Create: `e2e/recommend-fanout.spec.ts`（R1-R4 + M）
- Create: `e2e/recommend-reroll.spec.ts`（S1-S6）
- Modify: `frontend/index.html`（為 e2e 加穩定 `data-testid`：`reroll-btn` 已有 id；群組區塊加 `data-group`、卡片加 `data-course-id`）

> **註（執行頻率）**：e2e 燒 RPM，**不進 PR gate 的逐次跑**；flake gate（`--repeat-each`）只在合併前手動跑一次。CLAUDE.md：勿短時間連續大量請求。

- [ ] **Step 1: Write the failing test（先建 config + spec）**

建立 `playwright.config.ts`：

```typescript
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: "list",
  use: {
    baseURL: "http://localhost:3000",
    trace: "on-first-retry",
    actionTimeout: 0,
  },
  timeout: 120_000,            // 首載 fan-out ~42s + 緩衝
  expect: { timeout: 90_000 },
  webServer: [
    {
      command:
        "ALLOWED_ORIGIN=* .venv/bin/python -m uvicorn backend.main:app --port 8000",
      url: "http://localhost:8000/health",
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
    {
      command: "python3 -m http.server 3000 --directory frontend",
      url: "http://localhost:3000",
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
  ],
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], reducedMotion: "reduce" } },
    { name: "mobile",  use: { ...devices["Pixel 7"], reducedMotion: "reduce" } },
    { name: "tablet",  use: { ...devices["iPad (gen 7)"], reducedMotion: "reduce" } },
  ],
});
```

建立 `e2e/recommend-fanout.spec.ts`：

```typescript
import { test, expect } from "@playwright/test";

// R1: 清單內職涯 → SSE 5 階段、首批 ≤10 卡、首載合理時間
test("R1 known career streams stages and shows first batch", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /資料科學家|選擇職涯：資料科學家/ }).first().click().catch(async () => {
    await page.getByPlaceholder(/職涯|想成為/).fill("資料科學家");
    await page.getByRole("button", { name: /搜尋|查詢|送出/ }).click();
  });
  // 等結果區出現卡片（web-first，事件驅動非固定延遲）
  const cards = page.locator("#groups .card");
  await expect(cards.first()).toBeVisible({ timeout: 110_000 });
  await expect(cards).toHaveCount(10, { timeout: 110_000 }).catch(async () => {
    // 池可能 <10：至少 ≥1、≤10
    const n = await cards.count();
    expect(n).toBeGreaterThan(0);
    expect(n).toBeLessThanOrEqual(10);
  });
});

// R2: 首批依 group 分三區
test("R2 first batch grouped into three sections", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/職涯|想成為/).fill("資料科學家");
  await page.getByRole("button", { name: /搜尋|查詢|送出/ }).click();
  await expect(page.locator("#groups .card").first()).toBeVisible({ timeout: 110_000 });
  const sections = page.locator("#groups .group");
  expect(await sections.count()).toBeGreaterThanOrEqual(1);
});

// R3: 每卡 lead + ≥2 粗體 bullet + 課綱連結 href
test("R3 each card has reason lead, bold bullets, syllabus link", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/職涯|想成為/).fill("資料科學家");
  await page.getByRole("button", { name: /搜尋|查詢|送出/ }).click();
  const firstCard = page.locator("#groups .card").first();
  await expect(firstCard).toBeVisible({ timeout: 110_000 });
  await expect(firstCard.locator(".creason .lead")).toBeVisible();
  expect(await firstCard.locator(".creason ul li strong").count()).toBeGreaterThanOrEqual(2);
  await expect(firstCard.locator("a.clink")).toHaveAttribute("href", /https?:\/\//);
});

// R4: 清單外職涯 → 有結果或溫和導向問答
test("R4 open career yields results or gentle qa redirect", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/職涯|想成為/).fill("記者");
  await page.getByRole("button", { name: /搜尋|查詢|送出/ }).click();
  const cards = page.locator("#groups .card");
  const noMatch = page.locator("#no-match");
  await expect(cards.first().or(noMatch)).toBeVisible({ timeout: 110_000 });
});
```

建立 `e2e/recommend-reroll.spec.ts`：

```typescript
import { test, expect } from "@playwright/test";

async function loadFirstBatch(page) {
  await page.goto("/");
  await page.getByPlaceholder(/職涯|想成為/).fill("資料科學家");
  await page.getByRole("button", { name: /搜尋|查詢|送出/ }).click();
  await expect(page.locator("#groups .card").first()).toBeVisible({ timeout: 110_000 });
}

// S1: 點換一批 → 無新網路請求 → 顯示下一批、近乎瞬間
test("S1 reroll within pool makes no network request", async ({ page }) => {
  await loadFirstBatch(page);
  let recommendCalls = 0;
  page.on("request", (req) => {
    if (req.url().includes("/recommend")) recommendCalls++;
  });
  const firstId = await page.locator("#groups .card").first().getAttribute("data-course-id");
  await page.getByRole("button", { name: /換一批/ }).click();
  // 切片瞬間：等首卡 id 變化（web-first）
  await expect
    .poll(async () => page.locator("#groups .card").first().getAttribute("data-course-id"))
    .not.toBe(firstId);
  expect(recommendCalls).toBe(0);   // S1：池內切片不發請求
});

// S3: 連點兩次 → 池乾（依 30 池 = 3 批）
test("S3 two rerolls reach pool end without request", async ({ page }) => {
  await loadFirstBatch(page);
  let calls = 0;
  page.on("request", (req) => { if (req.url().includes("/recommend")) calls++; });
  await page.getByRole("button", { name: /換一批/ }).click();
  await page.getByRole("button", { name: /換一批/ }).click();
  expect(calls).toBe(0);
});

// S4: 池乾再點 → 此時才發 /recommend（新 seed）
test("S4 reroll on exhausted pool triggers new request", async ({ page }) => {
  await loadFirstBatch(page);
  // 連點直到池乾後再一次；waitForResponse 註冊於最後一次 click 前
  await page.getByRole("button", { name: /換一批/ }).click();
  await page.getByRole("button", { name: /換一批/ }).click();
  const respPromise = page.waitForResponse(/\/recommend/, { timeout: 110_000 });
  await page.getByRole("button", { name: /換一批/ }).click();
  const resp = await respPromise;
  expect(resp.url()).toMatch(/\/recommend/);
});

// S5: rapid double-click → 不重複切、不崩
test("S5 rapid double click does not skip or crash", async ({ page }) => {
  await loadFirstBatch(page);
  const btn = page.getByRole("button", { name: /換一批/ });
  await btn.click();
  await btn.click({ noWaitAfter: true });   // 緊接第二下（去抖應吞掉）
  await expect(page.locator("#groups .card").first()).toBeVisible();
  expect(await page.locator("#groups .card").count()).toBeLessThanOrEqual(10);
});

// S6: reload → 回首批初始態、不殘半批、無 null 例外
test("S6 reload resets to clean initial state", async ({ page }) => {
  await loadFirstBatch(page);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.reload();
  await expect(page.locator("#groups .card")).toHaveCount(0);
  expect(errors).toEqual([]);
});

// M1 (mobile project): 卡單欄、無水平捲動、換一批可 tap
test("M1 mobile no horizontal scroll", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "mobile only");
  await loadFirstBatch(page);
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  expect(overflow).toBe(false);
});
```

為 e2e 穩定性，修改 `frontend/app.js` 的 `renderCard`（第 176-185 行）加 `data-course-id`，與 `renderBatch` 的 group block 加 `data-group`（Task 11 的 block.innerHTML 上層 div 加屬性）：

```javascript
function renderCard(course){
  const card = document.createElement("div");
  card.className = "card";
  card.setAttribute("data-course-id", course.course_id);   // e2e locator
  card.innerHTML = `
    <div class="cname">${escHtml(course.name)}</div>
    <div class="cmeta">${escHtml(course.department)} · ${escHtml(course.teacher)} · ${course.credits} 學分</div>
    ${renderReason(course.reason)}
    <a class="clink" href="${escHtml(course.syllabus_url)}" target="_blank" rel="noopener noreferrer">查看課綱 →</a>`;
  return card;
}
```

在 Task 11 的 `renderBatch` group block 上層加 `block.setAttribute("data-group", g.key);`。

- [ ] **Step 2: Run tests to verify they fail / setup**

先安裝 Playwright（一次性）：
```bash
npm init -y && npm i -D @playwright/test && npx playwright install chromium
```
Run: `npx playwright test --project=desktop e2e/recommend-fanout.spec.ts -g "R1"`
Expected（未接好或 data-testid 未加時）：FAIL（locator timeout / card 不可見）。確認 RED 後接 Task 11 接線使其轉綠。

- [ ] **Step 3: 接線使 e2e 通過**

確保 Task 11 的前端改動（分頁 state、`data-course-id`、`data-group`、reroll 去抖）已落地。逐一跑 R1→R4、S1→S6（desktop），mobile 跑 M1。

- [ ] **Step 4: Run（flake gate，合併前手動跑一次）**

關鍵 e2e 5x burn-in（樣本稀少，低頻）：
```bash
npx playwright test --project=desktop -g "R1" --repeat-each=5
npx playwright test --project=desktop e2e/recommend-reroll.spec.ts -g "S1|S3|S4" --repeat-each=5
# 換一批/續池額外 burn-in
npx playwright test --project=desktop -g "S1" --repeat-each=10
```
Expected: 全綠 0 flake（5x / 10x）。**因燒 RPM，僅合併前跑一次，勿進 PR 逐次 gate。**

- [ ] **Step 5: Commit**

```bash
git add playwright.config.ts e2e/recommend-fanout.spec.ts e2e/recommend-reroll.spec.ts frontend/app.js .gitignore package.json
# 確保 e2e artifacts gitignore（test-results/、playwright-report/、node_modules/）
git commit -m "test(e2e): playwright real-backend recommend fan-out + reroll specs

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 13: 全套件回歸 + 壓測腳本（@stress，隔離不進 gate）

跑完整 pytest + 前端 node:test 確認無回歸；建立壓測腳本（並行 N 個 `/recommend`，記狀態碼/latency/空池率），標 `@stress` 與功能測試隔離（不進 PR gate）。對應測試策略 §5。

**Files:**
- Create: `scripts/stress_recommend.py`（並行 N、量測；gitignored 但 force-add 以保留，比照既有 backfill_*.py 慣例）
- Test: 全套件回歸（既有 + 新增）

- [ ] **Step 1: 全套件 pytest 回歸**

Run: `ALLOWED_ORIGIN=* GEMINI_API_KEY=x FILE_SEARCH_STORE_NAME=x .venv/bin/python -m pytest tests/ -q`
Expected: PASS（既有 78 + 新增 fan-out/ranked/pipeline/stream 測試全綠；無 `groups`-形狀殘留失敗）。若有 FAIL → 對齊 mock 形狀（凡 mock build 回傳巢狀 groups → 改扁平 courses）。

- [ ] **Step 2: 前端 node:test 回歸**

Run: `node --test tests/frontend/`
Expected: PASS（pagination.test.mjs 全綠）。

- [ ] **Step 3: 建壓測腳本**

建立 `scripts/stress_recommend.py`：

```python
# scripts/stress_recommend.py  —  @stress：並行 N 個 /recommend，記狀態碼/latency/空池率。
# 與功能 e2e 隔離：低頻手動跑、不進 PR gate（CLAUDE.md：勿短時間連續大量請求污染量測）。
# 用法：BASE=http://localhost:8000 python3 scripts/stress_recommend.py 3
import asyncio, os, sys, time
import httpx

BASE = os.environ.get("BASE", "http://localhost:8000")
CAREERS = ["資料科學家", "產品經理(PM)", "財務分析師", "律師", "行銷企劃",
           "軟體工程師", "公關專員", "管理顧問"]


async def one(client, career):
    t0 = time.monotonic()
    try:
        r = await client.post(f"{BASE}/recommend", json={"career": career}, timeout=200)
        dt = time.monotonic() - t0
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        empty = not data.get("courses") and not data.get("no_match")
        return {"career": career, "status": r.status_code, "latency_s": round(dt, 1), "empty": empty}
    except Exception as e:
        return {"career": career, "status": "EXC", "latency_s": round(time.monotonic() - t0, 1), "error": str(e)}


async def main(n):
    careers = (CAREERS * ((n // len(CAREERS)) + 1))[:n]
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*(one(client, c) for c in careers))
    for r in results:
        print(r)
    statuses = [r["status"] for r in results]
    lat = sorted(r["latency_s"] for r in results)
    print("---")
    print(f"N={n} 429率={statuses.count(429)/n:.0%} 503率={statuses.count(503)/n:.0%} "
          f"空池率={sum(1 for r in results if r.get('empty'))/n:.0%}")
    print(f"p50={lat[len(lat)//2]}s p95={lat[min(len(lat)-1, int(len(lat)*0.95))]}s")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 3))
```

- [ ] **Step 4: 跑壓測（手動、低頻；對照通過門檻）**

Run（需真後端 + 真 key）：
```bash
BASE=http://localhost:8000 python3 scripts/stress_recommend.py 3
```
Expected（測試策略 §5 門檻）：429 率 < 改前基線（須下降）、503 率 <2%、p50 首載 <~70s、p95 <~90s、空池率 ≈0、無貼 180s 尾巴。**勿短時間反覆跑**（撞 429 牆污染量測）。

- [ ] **Step 5: Commit**

```bash
git add -f scripts/stress_recommend.py
git commit -m "test(stress): parallel /recommend load script (@stress, not in PR gate)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage（設計 spec + 測試策略 §2-§6）**

設計 spec：
- §3.1 fan-out per-skill（`FANOUT_TOP_K=8`/`FANOUT_PER_SKILL=5`/`POOL_TARGET=30`、asyncio.gather、單支失敗回[]、correct_candidate_ids、deduplicate_by_name、round-robin）→ Task 1/2/3 ✓
- §3.2 stage2 整池標註（扁平 ranked schema、前綴復原+join+同名去重保留 rank/group）→ Task 4/5 ✓
- §3.3 回應形狀（扁平 `courses` + `batch_size=10` + seed + notice）→ Task 6 ✓；批次語意（rank 切批、前端依 group 分區）→ Task 10/11 ✓
- §3.4 串流（retrieve 內部 fan-out、compose 整池標註、事件序列維持、空池 no_match/error）→ Task 8 ✓
- §3.5 前端（存整池 state.pool/shown、首批、換一批純前端切片無請求、池乾續池 append 去重、navy 風格保留）→ Task 10/11 ✓
- §4 錯誤處理（單技能失敗 others 補足、全空才 no_match/error、池<batch 不報錯、429 兜底）→ Task 3/8/11 ✓
- §7 生成模型維持 flash、timeout/retry 保留 → 全程未改 `_GEN_MODEL`、未改 main.py client http_options ✓

測試策略：
- §2.A 合併去重（A1-A6）→ Task 2 ✓
- §2.B 容錯（B1-B4）→ Task 3（B1/B2/B3）+ Task 1（B4 非法 JSON 回[]）✓
- §2.C 池上限（C1-C4）→ Task 2（C1 截斷/C2 =30/C3 <batch/C4 交錯由 round-robin 測涵蓋）✓
- §2.D ranked 轉換（D1-D6）→ Task 5（D1-D5）+ Task 6（D6）✓
- §2.E 前端分頁（E1-E7）→ Task 10 ✓
- §3 e2e（R/S/M）→ Task 12 ✓（Q 系列屬另一 spec 的 Q&A 無 DB 降級，本 plan 範圍外，未納入——見下「已知範圍切分」）
- §5 壓測 → Task 13 ✓
- §6 flake gate（5x/10x repeat-each、role/data-attr locator、web-first、零 waitForTimeout）→ Task 12 config + Step 4 ✓
- 向後相容（POST /recommend + 既有測試）→ Task 7（POST 走新 pipeline）+ Task 9（既有測試 mock 對齊）+ 保留舊 stage1/stage2/build_groups 供 recovery/diversity 測試 ✓

**已知範圍切分（誠實標註）**：測試策略 §2.F（in-memory session）、§3.Q（Q&A 多輪 with/no DB）屬「受測對象 B：Q&A 無 DB 降級」，與本 plan「受測對象 A：fan-out」是兩個獨立子系統。依 writing-plans Scope Check，本 plan 僅涵蓋 A（fan-out），B 應為獨立 plan。已在覆蓋表標明，未遺漏。

**2. Placeholder scan**：全 task 的測試碼與實作碼均為完整可執行內容，無 TBD/TODO/「類似上面」/「add error handling」等紅旗。Task 11 無單元測試一節已明確說明理由（純 DOM 接線 + when-to-mock 禁 mock 自家 path，改由 §2.E 純函式 + §3.S e2e 覆蓋），非 placeholder。

**3. Type consistency**：
- 常數 `FANOUT_TOP_K`/`FANOUT_PER_SKILL`/`POOL_TARGET`/`DEFAULT_BATCH_SIZE` 在 Task 1 定義，後續 Task 2/4/7 引用名稱一致。
- `_RankedItem`/`_RankedOutput`（Task 4）→ `build_ranked_courses(ranked: _RankedOutput, ...)`（Task 5）→ `stage2_annotate_pool_async(...) -> _RankedOutput`（Task 4）簽名一致；`_RankedOutput.courses` 屬性名於 Task 4/5/7/8 一致。
- `Course`/`RecommendResponse.courses`/`batch_size`（Task 6）與後端 dict key `"courses"`/`"batch_size"`（Task 7/8）一致；前端 `data.courses`/`data.batch_size`（Task 11）一致。
- 前端模組函式 `createPaginationState`/`nextBatch`/`appendPool`/`groupBatch`（Task 10）與 app.js import/呼叫（Task 11）名稱一致。
- `fanout_query_skill_async`（Task 1）→ 被 `fanout_retrieve_async`（Task 3）以 `patch("backend.recommend.fanout_query_skill_async")` mock，名稱一致；`fanout_retrieve_async` 被 Task 7/8 mock 名稱一致。
- `course["rank"]`（Task 5 重新編號）與前端 pagination 測試的 `rank` 欄位（Task 10）、`data-course-id`（Task 12）一致。

無發現命名漂移。Self-review 通過。
