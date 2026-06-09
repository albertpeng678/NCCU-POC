# 檢索層遷移 Gemini → OpenAI 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:subagent-driven-development 逐 task 實作（每 task 派新 subagent + 兩段審查）。步驟用 checkbox（`- [ ]`）追蹤。
> **治理**：每寫 code task 遵守 karpathy（想清楚再寫/最小實作/外科手術改動/目標驅動）+ TDD red→green。每階段收尾過 Playwright MCP e2e 5x。release 前過 frontend/backend/db 三審。
> **spec**：`docs/superpowers/specs/2026-06-08-retrieval-migration-openai-design.md`（決策、約束、成本、風險全在那）。

**Goal:** 把推薦+問答的檢索與生成從 Gemini 整套硬切到 OpenAI（Vector Stores + Responses + `gpt-5.4-mini`），根治 Gemini 不穩，保住多輪/out-of-scope/citation。

**Architecture:** 託管 Vector Store 存 2718 課（檔名=course_id）。推薦走 `vector_stores.search` 每技能 fan-out（免費）+ `gpt-5.4-mini` 整池標註；問答走 Responses `file_search` tool（內建 citation）+ Postgres 歷史多輪 + 分數門檻擋離題。`AsyncOpenAI` 在 FastAPI startup 建 singleton（避免 event-loop 污染）。硬切無 flag。

**Tech Stack:** Python 3.11 + FastAPI + asyncpg + `openai` SDK（AsyncOpenAI）+ OpenAI Vector Stores/Responses + Playwright MCP + Sentry MCP。

---

## 前置：開工準備（一次性）

- [ ] **P.1 開 worktree**（superpowers:using-git-worktrees）：`git worktree add ../nccu-openai-migration -b feat/retrieval-openai master`，後續所有 task 在此 worktree。
- [ ] **P.2 裝依賴**：`echo 'openai>=2.0' >> backend/requirements.txt && pip install 'openai>=2.0'`。
- [ ] **P.3 設 env**（本機 `.env`，勿 commit）：`OPENAI_API_KEY=<使用者的 key>`、`OPENAI_MODEL=gpt-5.4-mini`、`OPENAI_VECTOR_STORE_ID=`（Phase 0 建完回填）。
- [ ] **P.4 讀 playwright-skill**（動 Playwright 前的硬要求）：`/Users/albertpeng/.claude/skills/playwright-skill/SKILL.md` + 重點 `core/locators.md`、`core/when-to-mock.md`、`core/assertions-and-waiting.md`、`core/common-pitfalls.md`。寫進腦：禁 mock 自家 backend success、禁 `waitForTimeout`、role/data-attr locator、多階段 `test.step()`。

---

# Phase 0：建知識庫 + OpenAI client + 檢索模組

> 產出：OpenAI Vector Store（2718 課已灌）+ 可用的 client 與檢索包裝。可獨立測試。

## File Structure（Phase 0 新檔）

- `backend/openai_client.py` — `AsyncOpenAI` singleton 生命週期（startup 建、同 loop 用）。唯一職責：提供 client。
- `backend/retrieval_openai.py` — 檢索包裝：`search_skill`（直接搜尋）+ `course_ids_from_annotations`（問答 citation 萃取）。唯一職責：把 OpenAI 檢索結果正規化成 `{course_id, score, content}`。
- `scripts/build_openai_vector_store.py` — 離線建庫（讀 `docs_cache.jsonl`、檔名=course_id、batch 上傳、attributes）。force-add。
- `scripts/smoke_test_retrieval.py` — 繁中召回人工驗證探針。force-add。

### Task 0.1：OpenAI client singleton `[序列][skill: TDD]`

**Files:**
- Create: `backend/openai_client.py`
- Test: `tests/backend/test_openai_client.py`

- [ ] **Step 1: 寫失敗測試**

```python
# tests/backend/test_openai_client.py
import backend.openai_client as oc

def test_get_client_returns_singleton(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    oc._client = None  # reset
    c1 = oc.get_client()
    c2 = oc.get_client()
    assert c1 is c2                      # 同一 instance（singleton）
    assert type(c1).__name__ == "AsyncOpenAI"

def test_get_client_no_key_returns_none(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    oc._client = None
    assert oc.get_client() is None       # 無 key 優雅回 None（不崩）
```

- [ ] **Step 2: 跑測試看失敗** — `pytest tests/backend/test_openai_client.py -v` → FAIL（module 不存在）。

- [ ] **Step 3: 最小實作**

```python
# backend/openai_client.py
"""AsyncOpenAI singleton。延遲建立、同一 event loop 內共用。
⚠️ 教訓（HANDOFF Session 8）：勿在 import 時建 client、勿跨 event loop 重用。
"""
import os
from openai import AsyncOpenAI

_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI | None:
    global _client
    if _client is not None:
        return _client
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    _client = AsyncOpenAI(api_key=key, max_retries=3, timeout=180.0)
    return _client
```

- [ ] **Step 4: 跑測試看通過** — `pytest tests/backend/test_openai_client.py -v` → PASS。
- [ ] **Step 5: commit** — `git add backend/openai_client.py tests/backend/test_openai_client.py && git commit -m "feat(openai): AsyncOpenAI singleton（startup 建、同 loop 用）"`

### Task 0.2：建庫腳本 `[序列][skill: TDD]`

**Files:**
- Create: `scripts/build_openai_vector_store.py`
- Test: `tests/backend/test_build_openai_store.py`

設計：純函式可單測（檔名規則、批次切分、attributes 組裝）；I/O（上傳）用 fake client 不打真 API。

- [ ] **Step 1: 寫失敗測試**

```python
# tests/backend/test_build_openai_store.py
from scripts.build_openai_vector_store import filename_for, file_attributes, chunk_batches

def test_filename_is_course_id():
    assert filename_for("070415001") == "070415001.txt"

def test_attributes_carry_course_id_and_url():
    attrs = file_attributes({"course_id": "070415001", "syllabus_url": "http://x/1"})
    assert attrs["course_id"] == "070415001"
    assert attrs["syllabus_url"] == "http://x/1"

def test_batches_split_under_2000():
    items = list(range(2718))
    batches = chunk_batches(items, size=2000)
    assert [len(b) for b in batches] == [2000, 718]   # 2 批，避開 300req/min + 2000/批上限
```

- [ ] **Step 2: 跑測試看失敗** — `pytest tests/backend/test_build_openai_store.py -v` → FAIL。

- [ ] **Step 3: 最小實作**（純函式 + main 灌庫流程）

```python
# scripts/build_openai_vector_store.py
"""離線建庫：讀 ingestion/docs_cache.jsonl（2718 課）→ 檔名=course_id 上傳 → 掛 attributes → 灌進 Vector Store。
用法：OPENAI_API_KEY=.. python scripts/build_openai_vector_store.py   # 印出 vector_store_id
冪等：ONLY_MISSING=1 跳過已存在 course_id。庫 <1GB → 儲存免費。
"""
import os, sys, json, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openai import AsyncOpenAI

CACHE = "ingestion/docs_cache.jsonl"

def filename_for(course_id: str) -> str:
    return f"{course_id}.txt"

def file_attributes(rec: dict) -> dict:
    return {"course_id": rec["course_id"], "syllabus_url": rec.get("syllabus_url", "")}

def chunk_batches(items: list, size: int = 2000) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]

def load_docs(path: str = CACHE) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out

async def main():
    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=5, timeout=180.0)
    docs = load_docs()
    vs = await client.vector_stores.create(name="nccu-courses-1142")
    print(f"vector_store_id={vs.id}  docs={len(docs)}")
    # 上傳每課成檔（檔名=course_id），收 file_id；再分批掛進 store 並帶 attributes
    for batch in chunk_batches(docs, size=2000):
        file_ids = []
        for rec in batch:
            up = await client.files.create(
                file=(filename_for(rec["course_id"]), rec["doc_text"].encode("utf-8")),
                purpose="assistants",
            )
            file_ids.append((up.id, rec))
        b = await client.vector_stores.file_batches.create(
            vector_store_id=vs.id,
            file_ids=[fid for fid, _ in file_ids],
            attributes=None,  # 逐檔 attributes 見下行：用 files.update 或建立時帶
        )
        # 逐檔補 attributes（course_id/syllabus_url）
        for fid, rec in file_ids:
            await client.vector_stores.files.update(
                vector_store_id=vs.id, file_id=fid, attributes=file_attributes(rec))
        # poll batch 完成
        while b.status in ("in_progress", "queued"):
            await asyncio.sleep(3)
            b = await client.vector_stores.file_batches.retrieve(
                vector_store_id=vs.id, batch_id=b.id)
        print(f"  batch {b.status}: {b.file_counts}")
    print(f"DONE. 填 .env: OPENAI_VECTOR_STORE_ID={vs.id}")

if __name__ == "__main__":
    asyncio.run(main())
```

> ⚠️ 實作 subagent 注意：上方 OpenAI SDK 呼叫（`vector_stores.file_batches.create` 的 attributes 參數位置、`files.create` 的 file tuple 形狀）需對照**當下** OpenAI 官方文件 / `openai` SDK 型別確認簽名（API 演進快）。這是必做的驗證步驟，非可省略。

- [ ] **Step 4: 跑純函式測試看通過** — `pytest tests/backend/test_build_openai_store.py -v` → PASS。
- [ ] **Step 5: commit** — `git add scripts/build_openai_vector_store.py tests/backend/test_build_openai_store.py && git commit -m "feat(scripts): OpenAI Vector Store 建庫腳本（檔名=course_id + batch + attributes）"`
- [ ] **Step 6: 真實灌庫（一次性、非測試）** — `OPENAI_API_KEY=.. python scripts/build_openai_vector_store.py`；把印出的 `vector_store_id` 填進 `.env` 的 `OPENAI_VECTOR_STORE_ID`。

### Task 0.3：檢索模組 `[序列][skill: TDD]`

**Files:**
- Create: `backend/retrieval_openai.py`
- Test: `tests/backend/test_retrieval_openai.py`

- [ ] **Step 1: 寫失敗測試**（mock OpenAI client，驗正規化 + citation 萃取）

```python
# tests/backend/test_retrieval_openai.py
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from backend.retrieval_openai import search_skill, course_ids_from_annotations

def test_search_skill_normalizes_results():
    fake = SimpleNamespace(vector_stores=SimpleNamespace(search=AsyncMock(return_value=SimpleNamespace(
        data=[SimpleNamespace(score=0.9, attributes={"course_id": "070415001"},
                              content=[SimpleNamespace(text="資料科學課綱")])]))))
    out = asyncio.run(search_skill(fake, "vs_1", "資料分析", top_k=8, score_threshold=0.0))
    assert out == [{"course_id": "070415001", "score": 0.9, "content": "資料科學課綱"}]

def test_course_ids_from_annotations_strips_ext():
    anns = [SimpleNamespace(type="file_citation", filename="070415001.txt", file_id="f1"),
            SimpleNamespace(type="file_citation", filename="070415002.txt", file_id="f2")]
    assert course_ids_from_annotations(anns) == ["070415001", "070415002"]

def test_course_ids_dedup_and_ignore_nonfile():
    anns = [SimpleNamespace(type="file_citation", filename="070415001.txt", file_id="f1"),
            SimpleNamespace(type="url_citation", filename=None, file_id=None),
            SimpleNamespace(type="file_citation", filename="070415001.txt", file_id="f1")]
    assert course_ids_from_annotations(anns) == ["070415001"]
```

- [ ] **Step 2: 跑測試看失敗** — `pytest tests/backend/test_retrieval_openai.py -v` → FAIL。

- [ ] **Step 3: 最小實作**

```python
# backend/retrieval_openai.py
"""OpenAI 檢索包裝：把 vector_stores.search 結果與 Responses annotations 正規化。
course_id 是 chunk↔課綱唯一鍵（設計決策 #19）：search 取自 attributes，citation 取自檔名（=course_id.txt）。
"""

async def search_skill(client, vs_id: str, query: str, top_k: int = 8,
                       score_threshold: float = 0.0) -> list[dict]:
    resp = await client.vector_stores.search(
        vector_store_id=vs_id, query=query, max_num_results=top_k,
        ranking_options={"score_threshold": score_threshold})
    out = []
    for r in resp.data:
        cid = (getattr(r, "attributes", None) or {}).get("course_id")
        if not cid:
            continue
        text = "".join(getattr(c, "text", "") for c in (getattr(r, "content", None) or []))
        out.append({"course_id": cid, "score": getattr(r, "score", 0.0), "content": text})
    return out


def course_ids_from_annotations(annotations) -> list[str]:
    seen, ids = set(), []
    for a in (annotations or []):
        if getattr(a, "type", None) != "file_citation":
            continue
        fn = getattr(a, "filename", None)
        if not fn:
            continue
        cid = fn[:-4] if fn.endswith(".txt") else fn
        if cid not in seen:
            seen.add(cid); ids.append(cid)
    return ids
```

- [ ] **Step 4: 跑測試看通過** — `pytest tests/backend/test_retrieval_openai.py -v` → PASS。
- [ ] **Step 5: commit** — `git add backend/retrieval_openai.py tests/backend/test_retrieval_openai.py && git commit -m "feat(retrieval): OpenAI 檢索正規化 + annotation→course_id 萃取"`

### Task 0.4：繁中召回 smoke test `[序列][skill: verification-before-completion]`

**Files:** Create: `scripts/smoke_test_retrieval.py`（force-add）

- [ ] **Step 1: 寫探針**（拿真實課綱 query 打真庫，人工看 top 結果）

```python
# scripts/smoke_test_retrieval.py
"""繁中召回人工驗證：5-10 題真實課綱問題打真 Vector Store，印 top 結果課名供人工確認。"""
import os, sys, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openai import AsyncOpenAI
from backend.retrieval_openai import search_skill

QUERIES = ["資料分析 統計 機器學習", "公司法 證券交易法", "新聞採訪 報導寫作",
           "心理諮商 輔導", "演算法 資料結構", "行銷 品牌管理", "國際關係 外交"]

async def main():
    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    vs = os.environ["OPENAI_VECTOR_STORE_ID"]
    for q in QUERIES:
        hits = await search_skill(client, vs, q, top_k=5, score_threshold=0.0)
        print(f"\n[{q}]")
        for h in hits:
            print(f"  {h['course_id']}  score={h['score']:.3f}  {h['content'][:40]}")

if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: 跑探針 + 人工 gate** — `OPENAI_API_KEY=.. OPENAI_VECTOR_STORE_ID=.. python scripts/smoke_test_retrieval.py`。**人工確認**每題 top 結果課程相關、course_id 對得上 `courses_meta.json`。不相關 → 調 `score_threshold`/`top_k` 或回報使用者討論。**通過才進 Phase 1。**
- [ ] **Step 3: commit 探針** — `git add scripts/smoke_test_retrieval.py && git commit -m "test(retrieval): 繁中召回 smoke 探針"`

---

# Phase 1：推薦模式切 OpenAI

> 產出：職涯推薦完全跑在 OpenAI（檢索+標註），前端零改動。e2e 5x gate。

### Task 1.1：fan-out 檢索改 OpenAI `[序列][skill: TDD][auditor: backend]`

**Files:** Modify: `backend/recommend.py`（`fanout_query_skill_async`/`fanout_retrieve_async`）；Test: `tests/backend/test_recommend_openai.py`

- [ ] **Step 1: 寫失敗測試**

```python
# tests/backend/test_recommend_openai.py
import asyncio
from unittest.mock import AsyncMock, patch
from backend.recommend import fanout_retrieve_async

def test_fanout_uses_openai_search_per_skill():
    # 每技能一支 search，合併去重後回候選池（course_id）
    async def fake_search(client, vs, q, top_k, score_threshold):
        return [{"course_id": "070415001", "score": 0.9, "content": "x"}]
    with patch("backend.recommend.search_skill", new=AsyncMock(side_effect=fake_search)) as s:
        pool = asyncio.run(fanout_retrieve_async(object(), "vs_1", "PM", ["技能A", "技能B"]))
    assert s.await_count == 2                      # 2 技能 = 2 支檢索
    assert any(c["course_id"] == "070415001" for c in pool)
```

- [ ] **Step 2: 跑看失敗** — `pytest tests/backend/test_recommend_openai.py -v` → FAIL。
- [ ] **Step 3: 改實作** — `fanout_query_skill_async` 內把 Gemini `generate_content(FileSearch)` 換成 `from backend.retrieval_openai import search_skill` → `await search_skill(client, vs_id, skill, top_k=FANOUT_TOP_K, score_threshold=REC_SCORE_THRESHOLD)`；`fanout_retrieve_async` 維持 `asyncio.gather` 容錯 + `merge_fanout_results`（round-robin + `deduplicate_by_prefix` + `deduplicate_by_name`，**不動**）。新增常數 `REC_SCORE_THRESHOLD = 0.0`（Phase 1 先不濾，交給標註）。
- [ ] **Step 4: 跑看通過** — `pytest tests/backend/test_recommend_openai.py -v` → PASS。
- [ ] **Step 5: commit** — `git commit -am "feat(recommend): fan-out 檢索改 OpenAI vector_stores.search"`

### Task 1.2：整池標註改 gpt-5.4-mini `[序列][skill: TDD][auditor: backend]`

**Files:** Modify: `backend/recommend.py`（`stage2_annotate_pool_async`）；同測試檔。

- [ ] **Step 1: 寫失敗測試**

```python
def test_stage2_annotate_returns_ranked_via_openai():
    from backend.recommend import stage2_annotate_pool_async
    import json, asyncio
    from unittest.mock import AsyncMock, patch
    fake_parsed = {"items": [{"course_id": "070415001", "rank": 1, "group": "core",
                              "reason": {"lead": "適合", "points": []}}]}
    fake_resp = type("R", (), {"output_parsed": fake_parsed, "output_text": json.dumps(fake_parsed)})()
    client = type("C", (), {})()
    with patch("backend.recommend._openai_structured", new=AsyncMock(return_value=fake_parsed)):
        ranked = asyncio.run(stage2_annotate_pool_async(
            client, "PM", ["技能A"], [{"course_id": "070415001", "score": 0.9, "content": "x"}]))
    assert ranked[0]["course_id"] == "070415001"
    assert ranked[0]["group"] == "core"
```

- [ ] **Step 2: 跑看失敗** → FAIL。
- [ ] **Step 3: 改實作** — 新增 helper `_openai_structured(client, model, system, user, schema)`：呼叫 `client.responses.create(model=OPENAI_MODEL, input=[...], text={"format": {"type":"json_schema", "schema": schema}})` 回 `resp.output_parsed`（無則 `json.loads(resp.output_text)`）。`stage2_annotate_pool_async` 改用之（沿用現 `_RankedOutput` schema 形狀 → `build_ranked_courses` 不動）。`OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4-mini")`。
- [ ] **Step 4: 跑看通過** → PASS。
- [ ] **Step 5: commit** — `git commit -am "feat(recommend): 整池標註改 gpt-5.4-mini 結構化輸出"`

### Task 1.3：清單外技能推導改 OpenAI `[並行於 1.1/1.2 之後][skill: TDD][auditor: backend]`

**Files:** Modify: `backend/recommend.py`（`derive_skills_for_career_async`）。

- [ ] **Step 1: 寫失敗測試** — `test_derive_skills_uses_openai`：mock `_openai_structured` 回 `{"skills": ["可轉移A","可轉移B"]}`，斷言回 list；亂打/空 → 回 `None`。
- [ ] **Step 2: 跑看失敗** → FAIL。
- [ ] **Step 3: 改實作** — 換 `_openai_structured`（schema `{"skills": [...]}`）；推不出回 `None`（沿用現 no_match 路徑）。
- [ ] **Step 4: 跑看通過** → PASS。
- [ ] **Step 5: commit** — `git commit -am "feat(recommend): 清單外技能推導改 OpenAI"`

### Task 1.4：main.py client 切換 + env + /health `[序列][skill: TDD][auditor: backend,db]`

**Files:** Modify: `backend/main.py`。

- [ ] **Step 1: 寫失敗測試** — `tests/backend/test_health_openai.py`：`GET /health` 回 `{"status":"ok","retrieval_backend":"openai","model":"gpt-5.4-mini"}`。
- [ ] **Step 2: 跑看失敗** → FAIL。
- [ ] **Step 3: 改實作** — `_client` 由 google-genai 換 `from backend.openai_client import get_client`（startup lifespan 取得）；`_STORE_NAME` → `OPENAI_VECTOR_STORE_ID`；`/health` 加 `retrieval_backend`/`model`；推薦呼叫傳 OpenAI client + vs_id。**保留 career_budget 命中分支不動**。
- [ ] **Step 4: 跑看通過 + 全後端回歸** — `pytest tests/backend/test_health_openai.py -v` PASS；`pytest tests/ -q` 全綠。
- [ ] **Step 5: commit** — `git commit -am "feat(main): client 切 OpenAI + /health 外露 backend/model"`

### Task 1.5：career_budget 離線重算改 OpenAI `[並行於 Phase1 e2e 前][skill: TDD][auditor: db]`

**Files:** Modify: `scripts/build_career_budget.py`。

- [ ] **Step 1: 確認介面不變** — `build_career_budget.py` 走 `stream_recommendation`（已改 OpenAI 底層），只需確認它讀新 client/vs。寫測試 `test_build_career_budget_openai`：mock pipeline 回扁平 courses，斷言 `upsert_budget` 被呼叫且 pool_size 正確。
- [ ] **Step 2-4: red→green**。
- [ ] **Step 5: commit** — `git commit -am "chore(scripts): career_budget 重算走 OpenAI pipeline"`
- [ ] **Step 6: 重灌 100 職涯（一次性）** — Phase 1 e2e 通過後：`OPENAI_API_KEY=.. OPENAI_VECTOR_STORE_ID=.. DATABASE_URL=<Railway> CONCURRENCY=2 python scripts/build_career_budget.py`。

### Task 1.6：Phase 1 e2e gate `[序列][skill: verification-before-completion + playwright-skill][auditor: frontend,backend]`

> **先確認已完成 P.4 讀 playwright-skill。** 真後端（OpenAI）+ 真前端。

- [ ] **Step 1: 起本機真後端** — `SENTRY_DSN='' OPENAI_API_KEY=.. OPENAI_VECTOR_STORE_ID=.. DATABASE_URL=<Railway public> python -m uvicorn backend.main:app --port 8000 --host 127.0.0.1`。
- [ ] **Step 2: Playwright MCP e2e（5x consecutive）** — 流程：開 `http://127.0.0.1:8000/` → 選清單內職涯（產品經理）→ 等卡片渲染 → 斷言：卡片數>0、每張 course_id 在 `courses_meta`、分桶三區、換一批純前端 0 網路、**0 console error**；再選清單外（記者）→ derive→檢索→出相關課或優雅 no_match。**連跑 5 次每次全綠 0 flake**。用 role/data-attr locator，禁 `waitForTimeout`，多階段 `test.step()`。
- [ ] **Step 3: Sentry MCP 查** — 跑完查 Sentry 無新錯誤類別（本機 `SENTRY_DSN=''` 不污染；此步在 staging/prod 部署後做）。
- [ ] **Step 4: 截 PNG → 使用者親眼確認「對」**（Live demo gate）→ 才算 Phase 1 GREEN。
- [ ] **Step 5: 記錄** — e2e 結果（每 run pass/total）寫進 PR 描述 / handoff。

---

# Phase 2：問答模式切 OpenAI

> 產出：問答跑在 OpenAI（Responses+file_search），多輪/citation/out-of-scope/防幻覺保住。e2e 5x gate。
>
> ⚠️ **OpenAI SSE 與 Gemini 不同（使用者特別提醒，格外注意）**：OpenAI 是**逐 token 平滑**輸出，Gemini 是**爆發式一坨 chunk**。現有前端 `createTypewriter`(20cps 緩衝)+漸進 markdown 是**為了平滑 Gemini 爆發**才做的——換 OpenAI 後此緩衝可能多餘/拖慢/雙重緩衝。Task 2.1/2.4 串流要**重新評估**：可能直接餵 token（少緩衝或不緩衝）、或調 cps。Task 2.5 e2e 須親驗串流平滑、無延遲堆積、表格仍逐列長出。
> 💡 **LLM provider 一律 OpenAI**（gpt-5.4-mini，使用者定）：含 `judge.py`/`qa_judge.py` + `career_budget` 預生成（Task 1.5）。Gemini 設計決策 #12「固定 2.5-flash」已被本遷移取代。

### Task 2.1：問答 Responses + file_search + 歷史 `[序列][skill: TDD][auditor: backend,db]`

**Files:** Modify: `backend/qa.py`（`stream_answer` 核心）；Test: `tests/backend/test_qa_openai.py`

- [ ] **Step 1: 寫失敗測試** — mock `client.responses.create`(stream) 吐 token + 帶 annotations；斷言：(a) 傳入 `tools=[{type:"file_search", vector_store_ids:[vs], max_num_results:5, ranking_options:{score_threshold}}]`；(b) input 含最近 N 輪歷史（`build_qa_contents`）；(c) 逐 token yield。
- [ ] **Step 2: 跑看失敗** → FAIL。
- [ ] **Step 3: 改實作** — `stream_answer` 改 `responses.create(model=OPENAI_MODEL, input=<system + 歷史 + 問題>, tools=[file_search...], stream=True)`；逐 event 取 `output_text.delta` yield；done 時收集 annotations。`_SYSTEM_INSTRUCTION` 保留「只依檢索內容作答」鐵則（#15，retrieve-then-read 語境重寫）。多輪沿用 `build_qa_contents`/`build_history_from_turns`。
- [ ] **Step 4: 跑看通過** → PASS。
- [ ] **Step 5: commit** — `git commit -am "feat(qa): 問答改 OpenAI Responses + file_search + Postgres 歷史多輪"`

### Task 2.2：citation 對應 `[序列][skill: TDD][auditor: backend,db]`

**Files:** Modify: `backend/qa.py`（citation 萃取段）。

- [ ] **Step 1: 寫失敗測試** — `test_qa_citation_maps_to_meta`：annotations `["070415001.txt"]` → `course_ids_from_annotations` → `courses_meta` enrich → citation 含課名/系所/syllabus_url。
- [ ] **Step 2-4: red→green** — 把現 `extract_grounding_course_ids` 換成 `from backend.retrieval_openai import course_ids_from_annotations` → 餵 `extract_citations`（後段 meta enrich 不動）。
- [ ] **Step 5: commit** — `git commit -am "feat(qa): citation 改讀 OpenAI annotations→course_id→meta"`

### Task 2.3：out-of-scope 門檻 `[序列][skill: TDD][auditor: backend]`

**Files:** Modify: `backend/qa.py`。

- [ ] **Step 1: 寫失敗測試** — `test_oos_no_citation_overrides`：file_search 無命中（annotations 空）或分數低 → 回「課綱查無相關資料」**不採信模型自由文字**；正常題不誤觸發。
- [ ] **Step 2-4: red→green** — 用 `ranking_options.score_threshold`（新常數 `QA_SCORE_THRESHOLD`）+ 沿用 `should_override_no_results` 精神（改判 annotations 空）。**程式碼判分數、不塞 system prompt**（spec 約束 #3）。
- [ ] **Step 5: commit** — `git commit -am "feat(qa): out-of-scope 用分數門檻+citation空覆寫（程式碼判，不入prompt）"`

### Task 2.4：防幻覺 + 串流收尾 `[序列][skill: TDD][auditor: backend,frontend]`

**Files:** Modify: `backend/qa.py`。

- [ ] **Step 1: 寫失敗測試** — `test_strip_fabricated_kept`：答案表格含不在 `courses_meta` 的假課 → 被剝除（沿用 `strip_fabricated_courses`，確認在 OpenAI 路徑仍套用）；串流 done 權威覆蓋 citation/followup。
- [ ] **Step 2-4: red→green** — 確認 `strip_fabricated_courses` 接到 OpenAI 答案；串流 token→前端 `progressive-md.js` 不動；清掉 Gemini 專屬 `_visible_text_from_chunk`（thought 過濾，OpenAI 不需要）。
- [ ] **Step 5: commit** — `git commit -am "feat(qa): 防幻覺剝假課沿用 + 串流收尾（清 Gemini thought 過濾）"`

### Task 2.5：Phase 2 e2e gate `[序列][skill: verification-before-completion + playwright-skill][auditor: frontend,backend,db]`

- [ ] **Step 1: 真後端起（同 1.6）**。
- [ ] **Step 2: Playwright MCP e2e（5x consecutive）** — `test.step()` 分段：①單輪問課→答案+citation 連結對回真課綱+followup ②**多輪**「想當PM修什麼」→「那金融呢」→ 第二答金融脈絡化（非重複 PM）③**離題**「今天天氣」→「查無資料」不幻覺 ④reload 後 `qa_session`/`qa_turn` 持久化讀回 ⑤0 console error。**5 次全綠 0 flake**。
- [ ] **Step 3: 黃金集 judge** — ~30 題（10 多輪+10 離題+10 一般）跑現有 `qa_judge`（RAGAS 3 維）+ 拒答正確率，記錄分數。
- [ ] **Step 4: Sentry MCP** — 部署後查無新錯誤類別 + 主動重現 429/5xx 確認友善錯誤泡泡。
- [ ] **Step 5: 截 PNG → 使用者確認「對」**（Live demo gate）→ Phase 2 GREEN。

---

# Phase 3：清理 + 三審 + release

### Task 3.1：移除 Gemini 死碼 `[序列][skill: TDD][auditor: backend]`

**Files:** Modify: `backend/qa.py`/`recommend.py`/`main.py`/`requirements.txt`；可能刪 `ingestion/uploader.py` Gemini 段。

- [ ] **Step 1: 確認 orphan 清單**（karpathy：只清本遷移製造的 orphan + 明列）— `answer_question`（Interactions 死碼）、雙 SDK 形狀相容碼、`google.genai._interactions` import、`google-genai` 依賴、`GEMINI_API_KEY`/`FILE_SEARCH_STORE_NAME` env 引用。
- [ ] **Step 2: 跑全後端回歸先綠** — `pytest tests/ -q`。
- [ ] **Step 3: 逐項移除 + 每次跑測試保持綠**。`requirements.txt` 移 `google-genai`。
- [ ] **Step 4: commit** — `git commit -am "chore: 移除 Gemini 檢索死碼 + google-genai 依賴"`

### Task 3.2：文件更新 `[並行][skill: documentation][auditor: -]`

**Files:** Modify: `CLAUDE.md`、`HANDOFF.md`、`.env.example`。

- [ ] **Step 1: 更新** — 技術棧表（Gemini→OpenAI）、env（OPENAI_*）、設計決策（新增「檢索遷 OpenAI」條、標 #12「生成固定 2.5-flash」已被本次取代為 gpt-5.4-mini）、HANDOFF 新 session 段。
- [ ] **Step 2: commit** — `git commit -am "docs: 更新 CLAUDE/HANDOFF/.env.example 至 OpenAI 檢索"`

### Task 3.3：三審 release gate `[並行 dispatch][skill: dispatching-parallel-agents + requesting-code-review]`

> **全數通過才 release。** 並行派 3 個 auditor（superpowers:requesting-code-review 模板），Director（Opus）cold-read 結論。

- [ ] **Step 1: 並行 dispatch 三審**（一個訊息三個 Agent）：
  - **backend auditor**：審 OpenAI 串接正確性、`AsyncOpenAI` event-loop 安全、retry/錯誤處理、推薦+問答 pipeline、grounding 鐵則保留、out-of-scope 判定。
  - **db auditor**：審 Postgres 多輪歷史串接、career_budget、course_id 對應鍵完整性、asyncpg、資料無漏。
  - **frontend auditor**：審前端零行為改動、e2e PNG 證據、跨裝置。
- [ ] **Step 2: 收斂** — 任一不通過 → superpowers:receiving-code-review 退修 → 重審該維度。三審全 ✅ 才往下。
- [ ] **Step 3: 記錄三審結論**（PR 描述）。

### Task 3.4：release + Sentry 監看 `[序列][skill: finishing-a-development-branch + railway-deploy-prep]`

- [ ] **Step 1: 收束分支** — merge `feat/retrieval-openai` → master（或開 PR）；Railway 設 `OPENAI_API_KEY`/`OPENAI_VECTOR_STORE_ID`/`OPENAI_MODEL`。
- [ ] **Step 2: git push 部署** → Railway build。
- [ ] **Step 3: 線上驗證** — `curl https://nccu-course.up.railway.app/health` 回 `retrieval_backend=openai`；真機走推薦+問答一輪。
- [ ] **Step 4: Sentry MCP 監看** — 部署後 24h 查無新錯誤類別；主動重現 429/5xx 確認降噪。
- [ ] **Step 5: 清 worktree** — `git worktree remove ../nccu-openai-migration`。

---

## Self-Review（plan vs spec 覆蓋檢查）

- **建庫**（spec §1）→ Task 0.2/0.4 ✅
- **client event-loop 安全**（spec 約束 #6）→ Task 0.1 ✅
- **推薦 fan-out + 標註 + derive**（spec §3）→ Task 1.1/1.2/1.3 ✅
- **career_budget 保留**（spec §6）→ Task 1.5 ✅
- **問答 Responses+多輪+citation+OOS+防幻覺**（spec §4）→ Task 2.1-2.4 ✅
- **course_id 對應鍵**（約束 #1）→ Task 0.3/2.2 ✅
- **grounding 鐵則 #15**（約束 #2）→ Task 2.1 ✅
- **OOS 程式碼判分數**（約束 #3）→ Task 2.3 ✅
- **型號 gpt-5.4-mini**（決策）→ Task 1.2/2.1（`OPENAI_MODEL`）✅
- **前端零改動**（Non-goal）→ Task 1.6/2.5 frontend auditor 驗 ✅
- **三審 gate**（治理 A）→ Task 3.3 ✅
- **MCP 實證**（治理 B）→ Task 1.6/2.5（Playwright）+ 3.4（Sentry）✅
- **skill 對應**（治理 C）→ 各 task tag 標注 ✅
- **karpathy**（治理 D）→ Task 3.1 surgical 清理 + 全程 TDD ✅
- **並行/序列**（治理 E）→ 各 task tag + Task 3.3 並行三審 ✅

無 placeholder；型別一致（`search_skill`/`course_ids_from_annotations`/`_openai_structured`/`OPENAI_MODEL`/`OPENAI_VECTOR_STORE_ID` 跨 task 一致）。
