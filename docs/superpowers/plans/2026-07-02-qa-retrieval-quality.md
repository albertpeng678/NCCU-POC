# 問答檢索品質改造（通用版）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development。步驟用 `- [ ]` 追蹤。

**Goal:** 問答檢索改成「LLM query rewrite（≤5 路，通用涵蓋職涯/系所縮寫/模糊）→ 一次多路 vector_stores.search 廣召回 → LLM rerank 收斂 → 注入生成」，取代脆弱的 model 自驅 file_search + attribute 硬篩主力。

**Architecture:** 新 `backend/qa_retrieval.py` 三個獨立可測函式（rewrite_to_queries / multi_query_search / rerank_courses）；`backend/qa.py` 的 `stream_answer` 改用這條為預設檢索路徑（既有 file_search 路徑保留為最終 fallback）。attribute 硬篩（build_dept_filter）退為 search 的輔助 filter 參數。

**Tech Stack:** Python 3.11 + FastAPI、openai SDK（Responses API `responses.parse` + `vector_stores.search`，`gpt-5.4-mini`）、pytest。

## Global Constraints
- 模型一律 `OPENAI_MODEL`（`backend/recommend.py` 匯出，預設 gpt-5.4-mini）；client 用 `backend/openai_client.py::get_client()`。**不得引入 Gemini**（`tests/backend/test_no_gemini.py` 守門）。
- 全 async 主 loop，不得 `asyncio.run`/`to_thread` 開新 loop（設計決策 #22）。
- **`vector_stores.search` 的 `query` 是 string 或 array of string，array 上限 5**（實測 400 確認）。
- **fail-open，絕不 0 筆**：rewrite 失敗→單路原句；filter 後空→去 filter；search 空→退既有 file_search 純語意；rerank 失敗→用 search 原順序。
- rerank = **LLM-as-reranker**（複用 OPENAI_MODEL，不本地部署、不外部商用 API）。
- attribute 硬篩（`build_dept_filter`，既有 `backend/dept_query.py`）**退為輔助**：僅在 rewrite 可靠抽出系所/學制時當 search 的 `filters` 參數。
- 走手動 `vector_stores.search`（不用 Responses `file_search` tool）。
- 跑測試用 `.venv/bin/python`（bare python 不在 PATH）：`cd /Users/albertpeng/Desktop/claude_project/NCCU-POC && ALLOWED_ORIGIN=* .venv/bin/python -m pytest ... `

## 檔案結構
- 新增 `backend/qa_retrieval.py`：`QueriesOut`(schema)、`rewrite_to_queries`、`multi_query_search`、`RerankOut`(schema)、`rerank_courses`、`retrieve_and_rerank`（串起來的門面）。
- 修改 `backend/qa.py`：`stream_answer` 檢索段改呼叫 `retrieve_and_rerank`。
- 測試 `tests/backend/test_qa_retrieval.py`。

## 並行分工
Task 1-3（qa_retrieval 三函式）同檔、序列做；Task 4 整合需 1-3 完成；Task 5 e2e、Task 6 review 收尾。

---

## Task 1: rewrite_to_queries（LLM 把口語→≤5 路 query + 輔助 slots）

**Files:** Create `backend/qa_retrieval.py`；Test `tests/backend/test_qa_retrieval.py`

**Interfaces:**
- Produces: `class QueriesOut(BaseModel)`; `async rewrite_to_queries(question:str)->dict`（回 `{"queries":list[str]≤5非空, "department":str|None, "college":str|None, "degree_level":str|None}`；正規化用既有 dept_query.normalize_*）

- [ ] **Step 1: 失敗測試**（純函式部分：schema clamp + fallback，mock LLM）

```python
# tests/backend/test_qa_retrieval.py
import asyncio, backend.qa_retrieval as qr

def test_clamp_queries_to_5():
    assert qr._clamp_queries(["a","b","c","d","e","f"]) == ["a","b","c","d","e"]
    assert qr._clamp_queries([]) == []

def test_rewrite_fallback_on_none(monkeypatch):
    async def boom(*a,**k): raise RuntimeError("llm down")
    monkeypatch.setattr(qr, "_call_rewrite_llm", boom)
    out = asyncio.run(qr.rewrite_to_queries("推薦我傳碩十堂課"))
    assert out["queries"] == ["推薦我傳碩十堂課"]   # degrade 單路原句
    assert out["department"] is None and out["college"] is None
```

- [ ] **Step 2: 跑到 RED**：`... pytest tests/backend/test_qa_retrieval.py -v` → FAIL（module 不存在）
- [ ] **Step 3: 實作**

```python
# backend/qa_retrieval.py
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel
from backend.dept_query import normalize_department, normalize_college, normalize_degree

class QueriesOut(BaseModel):
    queries: list[str]           # ≤5 個聚焦檢索 query
    department: Optional[str] = None
    college: Optional[str] = None
    degree_level: Optional[str] = None

def _clamp_queries(qs: list[str]) -> list[str]:
    return [q.strip() for q in qs if q and q.strip()][:5]

_REWRITE_SYS = (
    "你是政大課程檢索助手。把使用者的口語課程需求，改寫成最多 5 個『聚焦、彼此互補、"
    "用政大課程課綱可能出現的正式詞彙』的檢索查詢字串（繁體中文）。規則："
    "①職涯/目標（如『想當PM』）→展開成該職涯的能力面向（產品管理、使用者體驗、數據分析、專案管理…）；"
    "②系所口語縮寫（如『傳碩』=傳播學院碩士、『資管』=資訊管理）→展開成該系所正式名＋代表主題；"
    "③用課綱語彙（如『使用者體驗』非『用戶研究』）。另外若使用者明確指定系所/學院/學制，抽出原文放對應欄位（沒有填 null）。"
    "queries 至少 1 個、最多 5 個。"
)

async def _call_rewrite_llm(question: str) -> QueriesOut:
    from backend.openai_client import get_client
    from backend.recommend import OPENAI_MODEL
    client = get_client()
    resp = await client.responses.parse(
        model=OPENAI_MODEL,
        input=[{"role":"system","content":_REWRITE_SYS},{"role":"user","content":question}],
        text_format=QueriesOut,
    )
    return resp.output_parsed

async def rewrite_to_queries(question: str) -> dict:
    try:
        parsed = await _call_rewrite_llm(question)
        qs = _clamp_queries(parsed.queries) if parsed else []
    except Exception:
        qs = []
    if not qs:
        return {"queries":[question], "department":None, "college":None, "degree_level":None}
    return {
        "queries": qs,
        "department": normalize_department(getattr(parsed,"department",None)),
        "college": normalize_college(getattr(parsed,"college",None)),
        "degree_level": normalize_degree(getattr(parsed,"degree_level",None)),
    }
```

- [ ] **Step 4: 跑到 GREEN**（2 passed）
- [ ] **Step 5: 整合小測（真 LLM）**：`... python -c "import asyncio,backend.qa_retrieval as qr; print(asyncio.run(qr.rewrite_to_queries('推薦我傳碩十堂課')))"`（`set -a; source .env; set +a` 先載金鑰）→ 確認 queries 展開成傳播碩士相關、college≈傳播學院。貼輸出。
- [ ] **Step 6: Commit** `feat(qa-retr): rewrite_to_queries（口語→≤5路query+輔助slots）`

---

## Task 2: multi_query_search（一次多路 vector_stores.search + 輔助 filter + fallback）

**Files:** Modify `backend/qa_retrieval.py`；Test 同檔

**Interfaces:**
- Consumes: Task1 slots、`backend/dept_query.build_dept_filter`
- Produces: `async multi_query_search(queries:list[str], slots:dict, vs_id:str, max_num_results:int=24)->list`（回 search result data list，空回 `[]`）

- [ ] **Step 1: 失敗測試**（mock client，驗證 query 傳陣列且 ≤5、輔助 filter 帶入、空結果去 filter 重搜）

```python
def test_multi_query_search_passes_array_and_filter(monkeypatch):
    calls=[]
    class FakeResp:
        def __init__(self,data): self.data=data
    class FakeVS:
        def search(self, vector_store_id, query, max_num_results, filters=None):
            calls.append({"query":query,"filters":filters}); return FakeResp([{"id":1}])
    class FakeClient: 
        vector_stores=FakeVS()
    async def acall(**k): return FakeClient().vector_stores.search(**k)
    monkeypatch.setattr(qr,"_vs_search", lambda **k: acall(**k))
    out=asyncio.run(qr.multi_query_search(["a","b","c","d","e","f"], {"department":"歷史學系"}, "vs_x"))
    assert len(calls[0]["query"])==5           # 陣列且 clamp 到 5
    assert calls[0]["filters"] is not None      # 有系所→帶輔助 filter
    assert out==[{"id":1}]
```

- [ ] **Step 2: RED**
- [ ] **Step 3: 實作**（append）

```python
from backend.dept_query import build_dept_filter

async def _vs_search(**kwargs):
    from backend.openai_client import get_client
    return await get_client().vector_stores.search(**kwargs)

async def multi_query_search(queries: list[str], slots: dict, vs_id: str, max_num_results: int = 24) -> list:
    q = _clamp_queries(queries) or None
    if not q: return []
    filt = build_dept_filter({k:slots.get(k) for k in ("department","college","degree_level")})
    async def _run(f):
        r = await _vs_search(vector_store_id=vs_id, query=q, max_num_results=max_num_results, filters=f)
        return list(getattr(r,"data",[]) or [])
    data = await _run(filt)
    if not data and filt is not None:   # 輔助 filter 撈空 → 去 filter 重搜（fail-open）
        data = await _run(None)
    return data
```

- [ ] **Step 4: GREEN**
- [ ] **Step 5: Commit** `feat(qa-retr): multi_query_search（一次多路array+輔助filter+去filter fallback）`

---

## Task 3: rerank_courses（LLM listwise 重排收斂）

**Files:** Modify `backend/qa_retrieval.py`；Test 同檔

**Interfaces:**
- Produces: `class RerankOut(BaseModel)`; `async rerank_courses(question:str, candidates:list, top_k:int=10)->list`（回重排後前 top_k 的 candidate 物件；fail→原順序前 top_k）

- [ ] **Step 1: 失敗測試**（mock LLM 回指定順序；驗證依回傳 course_id 順序重排 + fail-open）

```python
def test_rerank_reorders_by_llm(monkeypatch):
    cands=[{"course_id":"A","name":"n1","text":"t1"},{"course_id":"B","name":"n2","text":"t2"}]
    async def fake(q,c): 
        class O: ranked_course_ids=["B","A"]
        return O()
    monkeypatch.setattr(qr,"_call_rerank_llm", fake)
    out=asyncio.run(qr.rerank_courses("q",cands,top_k=2))
    assert [x["course_id"] for x in out]==["B","A"]

def test_rerank_failopen(monkeypatch):
    cands=[{"course_id":"A"},{"course_id":"B"}]
    async def boom(q,c): raise RuntimeError()
    monkeypatch.setattr(qr,"_call_rerank_llm", boom)
    assert qr.rerank_courses and asyncio.run(qr.rerank_courses("q",cands,top_k=5))==cands
```

- [ ] **Step 2: RED**
- [ ] **Step 3: 實作**（append；candidate 需有 course_id/name/text；listwise 只回排序後 id）

```python
class RerankOut(BaseModel):
    ranked_course_ids: list[str]   # 依與使用者意圖相關性由高到低；只保留相關的

_RERANK_SYS = (
    "你是課程相關性排序器。給定使用者的真實需求與一組候選課程，"
    "**只保留真正符合需求的課**、依相關性由高到低回傳其 course_id 順序（不相關的剔除）。"
    "嚴格依課程內容判斷，勿臆造 course_id。"
)

async def _call_rerank_llm(question: str, candidates: list) -> RerankOut:
    from backend.openai_client import get_client
    from backend.recommend import OPENAI_MODEL
    lines = [f'- {c["course_id"]}｜{c.get("name","")}｜{(c.get("text","") or "")[:180]}' for c in candidates]
    client = get_client()
    resp = await client.responses.parse(
        model=OPENAI_MODEL,
        input=[{"role":"system","content":_RERANK_SYS},
               {"role":"user","content":f"需求：{question}\n候選：\n"+"\n".join(lines)}],
        text_format=RerankOut,
    )
    return resp.output_parsed

async def rerank_courses(question: str, candidates: list, top_k: int = 10) -> list:
    if not candidates: return []
    try:
        parsed = await _call_rerank_llm(question, candidates)
        order = parsed.ranked_course_ids if parsed else []
        by_id = {c["course_id"]: c for c in candidates}
        ranked = [by_id[i] for i in order if i in by_id]
        return (ranked or candidates)[:top_k]
    except Exception:
        return candidates[:top_k]
```

- [ ] **Step 4: GREEN**
- [ ] **Step 5: Commit** `feat(qa-retr): rerank_courses（LLM listwise重排+剔除不相關+fail-open）`

---

## Task 4: 門面 retrieve_and_rerank + qa.py 整合

**Files:** Modify `backend/qa_retrieval.py`（加門面）、`backend/qa.py`（stream_answer 改用）；Test `tests/backend/test_qa_retrieval.py` + `tests/backend/test_qa_dept_filter.py`

**Interfaces:**
- Produces: `async retrieve_and_rerank(question:str, vs_id:str)->tuple[str|None,list[str]]`（回 `(context_text, course_ids)`；全空→`(None,[])` 讓 qa.py 退既有 file_search）
- Consumes（qa.py）：既有 `_format_dept_context`/`course_ids_from_search_results` 或等效。

- [ ] **Step 1: 失敗測試**（門面串接：mock rewrite/search/rerank，驗證有結果回 context+ids、全空回 (None,[])）

```python
def test_retrieve_and_rerank_happy(monkeypatch):
    async def fr(q): return {"queries":["x"],"department":None,"college":None,"degree_level":None}
    async def fs(qs,slots,vs,**k): return [{"course_id":"A","filename":"A.txt","content":[type("t",(),{"text":"hi"})()],"attributes":{"course_id":"A"}}]
    async def fk(q,c,top_k=10): return c
    monkeypatch.setattr(qr,"rewrite_to_queries",fr); monkeypatch.setattr(qr,"multi_query_search",fs); monkeypatch.setattr(qr,"rerank_courses",fk)
    ctx,ids=asyncio.run(qr.retrieve_and_rerank("q","vs"))
    assert ctx and "A" in ids

def test_retrieve_and_rerank_empty(monkeypatch):
    async def fr(q): return {"queries":["x"],"department":None,"college":None,"degree_level":None}
    async def fs(*a,**k): return []
    monkeypatch.setattr(qr,"rewrite_to_queries",fr); monkeypatch.setattr(qr,"multi_query_search",fs)
    assert asyncio.run(qr.retrieve_and_rerank("q","vs"))==(None,[])
```

- [ ] **Step 2: RED**
- [ ] **Step 3: 實作門面**（append qa_retrieval.py）

```python
def _fmt(cands: list) -> str:
    rows=[]
    for c in cands:
        a=c.get("attributes") or {}
        txt="".join(getattr(x,"text","") for x in (c.get("content") or []))[:1200]
        rows.append(f'課名：{c.get("filename","")}｜系所：{a.get("dept_canonical","")}\n{txt}')
    return "\n\n".join(rows)

def _norm_cands(data: list) -> list:
    out=[]
    for d in data:
        a=getattr(d,"attributes",None) or (d.get("attributes") if isinstance(d,dict) else {}) or {}
        cid=a.get("course_id") or (getattr(d,"filename","") or (d.get("filename","") if isinstance(d,dict) else "")).replace(".txt","")
        content=getattr(d,"content",None) if not isinstance(d,dict) else d.get("content")
        fn=getattr(d,"filename","") if not isinstance(d,dict) else d.get("filename","")
        out.append({"course_id":cid,"name":fn.replace(".txt",""),"filename":fn,"attributes":a,"content":content,
                    "text":"".join(getattr(x,"text","") for x in (content or []))})
    return out

async def retrieve_and_rerank(question: str, vs_id: str) -> tuple[str|None, list[str]]:
    slots = await rewrite_to_queries(question)
    data = await multi_query_search(slots["queries"], slots, vs_id)
    if not data:
        return None, []
    cands = _norm_cands(data)
    ranked = await rerank_courses(question, cands, top_k=10)
    if not ranked:
        return None, []
    # 前 6 碼去重（設計決策 #28）
    seen=set(); uniq=[]
    for c in ranked:
        k=(c["course_id"] or "")[:6]
        if k and k in seen: continue
        seen.add(k); uniq.append(c)
    return _fmt(uniq), [c["course_id"] for c in uniq if c["course_id"]]
```

- [ ] **Step 4: qa.py 整合**：在 `stream_answer` 檢索段（既有 `retrieve_dept_filtered_context` 呼叫處）改為**先試** `retrieve_and_rerank(question, vs_id)`；回非 (None,*) → 用注入 context 生成（沿用既有 `_build_openai_input(..., extra_system=_DEPT_FILTER_APPEND, context_text=ctx)` 注入路徑 + citations 用回傳 ids）；回 (None,[]) → 落回既有 file_search 純語意路徑（一字不改）。**先 Read qa.py 對齊實際函式名/注入方式**，不硬套。
- [ ] **Step 5: 跑到 GREEN + 全回歸**：`... pytest tests/backend -q` → 新測試綠、既有數字不減（test_no_gemini/test_recommend_loop_fix/既有 qa 測試無回歸）。
- [ ] **Step 6: Commit** `feat(qa-retr): retrieve_and_rerank 門面 + qa.py 預設走 rewrite→多路→rerank`

---

## Task 5: 真後端 + 真瀏覽器 e2e（通用多案例，5x consecutive）

- [ ] **Step 1** 起真後端（`set -a; source .env; set +a; export ALLOWED_ORIGIN=*; .venv/bin/python -m uvicorn backend.main:app --port 8100`）。
- [ ] **Step 2** curl GET `/qa/stream?question=...`（urlencode）逐案驗抽表格系所/課名：
  - 「想成為 PM 要修什麼課」→ 產品/UX/專案管理相關，**無**散文選讀/作業價值管理。
  - 「推薦我傳碩十堂課」→ 傳播碩士相關，**不混**資碩工/創新創造力研究中心/大學部。
  - 「想做資料分析」「推薦輕鬆的通識」→ 對題。
  - 寒暄/離題 → 正常、不 0 筆。
- [ ] **Step 3** 主案例（PM + 傳碩）各 **5x consecutive**，每次抽結果判對題，0 flake 才綠；記每次結果。
- [ ] **Step 4** 真瀏覽器（Playwright，先 `unrouteAll` 清 mock、注入 `__API_URL__`）跑「傳碩十堂課」，截圖 Director cold-Read 確認不混外系。

---

## Task 6: 最終 code review + 獨立稽核

- [ ] **Step 1** code review skill 對全 diff 跑五面向（correctness/readability/architecture/security/performance），重點：fail-open 各分支、既有 file_search 路徑未破壞、無 Gemini、async loop 安全、query array ≤5。
- [ ] **Step 2** 獨立 auditor（fresh context）：抽查 rewrite 對多種口語（職涯/系所縮寫/模糊）輸出合理；獨立重跑 e2e（PM/傳碩/其他系）確認對題且不混外系；查回歸三守門。
- [ ] **Step 3** finishing-a-development-branch 決定 merge/PR，live-demo gate 截圖給使用者確認。

## Self-Review（對照 spec）
- 根因 A(無rewrite)→Task1；B(無rerank)→Task3；C(attribute脆弱)→Task2 filter 退輔助 ✓
- 通用（職涯+系所縮寫+模糊）→Task1 rewrite prompt + Task5 多案例 e2e ✓
- rerank=A LLM-as-reranker→Task3 ✓；不本地/不外部API ✓
- fail-open 不 0 筆→Task1/2/3/4 各分支 + Task4 落回 file_search ✓
- query array ≤5→Task1 _clamp + Task2 ✓
- attribute 退輔助→Task2 build_dept_filter 當 search filters 參數、撈空去 filter ✓
- TDD+code review→各 Task red→green + Task6 ✓
