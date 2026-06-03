# 推薦多樣性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓同職涯重複查詢回傳不同但仍相關的課程組合，並在 UI 加「換一批」與「導向問答」引導。

**Architecture:** 後端 `stage1` 擴大候選池至 40，新增 `sample_candidates`（保留前 N 門定錨 + 用 seed 隨機抽樣其餘）插在 stage1 與 stage2 之間；`seed` 由 request 選用、未帶則後端產生亂數並於 response 回傳。前端加「換一批」按鈕（重呼叫 /recommend）與底部 CTA（切到問答模式）。

**Tech Stack:** Python 3.11 / FastAPI / pydantic / pytest；原生 HTML/CSS/JS；Playwright E2E。

> 規範：每個 code 步驟先看測試 RED 再 GREEN；每個 Task 結束 commit。前端視覺改動需截 PNG 給 user 確認後才 commit（本 session live demo gate）。

---

### Task 1: `sample_candidates` 純函式 + 常數

**Files:**
- Modify: `backend/recommend.py`（新增常數與函式）
- Test: `tests/backend/test_recommend_diversity.py`（新建）

- [ ] **Step 1: 寫失敗測試**

建立 `tests/backend/test_recommend_diversity.py`：

```python
# tests/backend/test_recommend_diversity.py
from backend.recommend import sample_candidates, ANCHOR_COUNT, SAMPLE_SIZE


def _pool(n):
    # 製造 n 門候選，relevance 由高到低（index 0 最相關）
    return [{"course_id": f"{i:09d}", "course_name": f"課{i}", "relevance": "x"} for i in range(n)]


def test_same_seed_is_reproducible():
    pool = _pool(40)
    a = sample_candidates(pool, seed=123)
    b = sample_candidates(pool, seed=123)
    assert [c["course_id"] for c in a] == [c["course_id"] for c in b]


def test_different_seed_changes_selection():
    pool = _pool(40)
    a = sample_candidates(pool, seed=1)
    b = sample_candidates(pool, seed=2)
    assert [c["course_id"] for c in a] != [c["course_id"] for c in b]


def test_anchors_always_included():
    pool = _pool(40)
    result_ids = {c["course_id"] for c in sample_candidates(pool, seed=7)}
    for i in range(ANCHOR_COUNT):
        assert f"{i:09d}" in result_ids


def test_result_size_and_no_duplicates():
    pool = _pool(40)
    result = sample_candidates(pool, seed=7)
    ids = [c["course_id"] for c in result]
    assert len(ids) == SAMPLE_SIZE
    assert len(set(ids)) == len(ids)


def test_small_pool_returned_as_is():
    pool = _pool(5)  # 少於 SAMPLE_SIZE → 原樣回傳
    result = sample_candidates(pool, seed=7)
    assert [c["course_id"] for c in result] == [c["course_id"] for c in pool]
```

- [ ] **Step 2: 跑測試確認 RED**

Run: `.venv/bin/python -m pytest tests/backend/test_recommend_diversity.py -q`
Expected: FAIL（`ImportError: cannot import name 'sample_candidates'`）

- [ ] **Step 3: 實作**

在 `backend/recommend.py` 的 import 區下方（`import time` 之後）加入 `import random`，並在 `extract_json_array` 之前加入常數與函式：

```python
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
```

- [ ] **Step 4: 跑測試確認 GREEN**

Run: `.venv/bin/python -m pytest tests/backend/test_recommend_diversity.py -q`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add backend/recommend.py tests/backend/test_recommend_diversity.py
git commit -m "feat(recommend): sample_candidates 候選池抽樣（定錨+seed 隨機）"
```

---

### Task 2: 串接 seed 與擴大 stage1 池

**Files:**
- Modify: `backend/recommend.py`（`stage1_retrieve` prompt 15→40；`build_recommendation` 與 `build_recommendation_instrumented` 加 seed 並插入 sample_candidates）

- [ ] **Step 1: 改 stage1 prompt 取 40**

在 `stage1_retrieve` 內，將 prompt 字串中的「最相關的 15 門課程」改為使用 `POOL_SIZE`：

```python
        "請從課程知識庫找出最相關的 "
        f"{POOL_SIZE} 門課程。\n"
```
（取代原本 `"請從課程知識庫找出最相關的 15 門課程。\n"` 那一行）

- [ ] **Step 2: `build_recommendation_instrumented` 加 seed 並抽樣**

把函式簽名與內文改為：

```python
def build_recommendation_instrumented(
    client: genai.Client, store_name: str, career: str, seed: int = 0
) -> tuple[dict, int]:
    """Same as build_recommendation but also returns stage1 candidate count."""
    t0 = time.monotonic()
    careers = load_careers()
    meta = load_courses_meta()
    if career not in careers:
        raise ValueError(f"Unknown career: {career}")
    skills = careers[career]["skills"]

    candidates = stage1_retrieve(client, store_name, career, skills)
    stage1_count = len(candidates)
    if not candidates:
        raise ValueError("Stage 1 returned no candidate courses")
    candidates = sample_candidates(candidates, seed)
    stage2 = stage2_group(client, career, skills, candidates)
    ...
```
（只改前段；`process_group` 與 `groups`/`return` 維持原樣，但 return 的 result 需加 seed，見 Step 3）

- [ ] **Step 3: result 帶入 seed**

把 `build_recommendation_instrumented` 結尾的 result 改為：

```python
    result = {"career": career, "groups": groups, "latency_ms": latency_ms, "seed": seed}
    return result, stage1_count
```

並同步把 `build_recommendation` 也加上 `seed: int = 0` 參數、在 `stage1_retrieve` 後插入 `candidates = sample_candidates(candidates, seed)`，result 加 `"seed": seed`（與 instrumented 一致，保持兩函式行為對齊）。

- [ ] **Step 4: 跑既有後端測試確認沒破壞**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS（全綠；既有測試不呼叫這兩個函式，新增 seed 預設值向後相容）

- [ ] **Step 5: Commit**

```bash
git add backend/recommend.py
git commit -m "feat(recommend): stage1 池擴大至 40 並串接 seed 抽樣"
```

---

### Task 3: models 與 main.py 串接 seed

**Files:**
- Modify: `backend/models.py`（RecommendRequest/Response 加 seed）
- Modify: `backend/main.py`（產生亂數 seed、傳入、回傳）

- [ ] **Step 1: models 加欄位**

`RecommendRequest` 加：
```python
class RecommendRequest(BaseModel):
    career: str
    seed: int | None = None
```
`RecommendResponse` 加：
```python
class RecommendResponse(BaseModel):
    career: str
    groups: CourseGroups
    latency_ms: int
    seed: int = 0
```

- [ ] **Step 2: main.py 產生並傳入 seed**

在 `backend/main.py` 頂部 import 區加 `import random`。把 `/recommend` 內呼叫改為：

```python
    seed = req.seed if req.seed is not None else random.randrange(1_000_000)
    result = None
    stage1_count = 0
    error = None
    try:
        result, stage1_count = build_recommendation_instrumented(
            _client, _STORE_NAME, req.career, seed
        )
    except Exception as e:
        error = e
```
（result 已含 `"seed"`，RecommendResponse 會自動帶出。）

- [ ] **Step 3: 跑全測試**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS（全綠）

- [ ] **Step 4: Live 驗證（同職涯雙 seed 不同）**

以 dry-run store 不易看出（課太少），改用一支臨時腳本對 full store（ingestion 完成後）驗證；dry-run 階段先確認回應含 seed 欄位即可：

Run（dry-run 階段）：
```bash
.venv/bin/python -c "
import os
from dotenv import load_dotenv; load_dotenv()
from google import genai
from backend.recommend import build_recommendation_instrumented
c=genai.Client(api_key=os.environ['GEMINI_API_KEY']); s=os.environ['FILE_SEARCH_STORE_NAME'].split(' #')[0].strip()
r,_=build_recommendation_instrumented(c,s,'公務員',seed=42); print('seed in result:', r.get('seed'))
"
```
Expected: 印出 `seed in result: 42`

- [ ] **Step 5: Commit**

```bash
git add backend/models.py backend/main.py
git commit -m "feat(recommend): /recommend 支援 seed（未帶則亂數）並回傳"
```

---

### Task 4: 前端「換一批推薦」按鈕

**Files:**
- Modify: `frontend/index.html`（#results 內加按鈕）
- Modify: `frontend/app.js`（綁定 + 沿用 fetchRecommendation）
- Modify: `frontend/style.css`（按鈕樣式）

- [ ] **Step 1: index.html 加按鈕**

把 `#results` 區塊（第 83–89 行）改為在 `#groups` 後加入按鈕：

```html
      <section id="results" class="results" hidden aria-live="polite">
        <div class="results-head">
          <h2>推薦課程 · <span id="results-career" class="for"></span></h2>
          <span id="results-latency" class="lat"></span>
        </div>
        <div id="groups" class="groups"></div>
        <div class="results-actions">
          <button id="reroll-btn" class="reroll-btn" type="button">換一批推薦 🔄</button>
        </div>
      </section>
```

- [ ] **Step 2: app.js 綁定換一批**

在 `searchBtn.addEventListener(...)`（第 222 行）後新增：

```javascript
const rerollBtn = document.getElementById("reroll-btn");
rerollBtn.addEventListener("click", ()=>{ if(selectedCareer) fetchRecommendation(selectedCareer); });
```
（fetchRecommendation 不帶 seed → 後端每次新亂數 → 不同一批；無需改 fetch body。）

- [ ] **Step 3: style.css 加樣式**

在 `frontend/style.css` 檔尾加入：

```css
.results-actions{display:flex;justify-content:center;margin-top:24px}
.reroll-btn{font-family:"Chakra Petch";font-size:14px;letter-spacing:.04em;color:var(--cyan);background:var(--glass);border:1.5px solid var(--glass-brd);border-radius:24px;padding:11px 26px;cursor:pointer;transition:border-color .18s,box-shadow .18s,transform .15s}
.reroll-btn:hover{border-color:var(--cyan);box-shadow:0 0 18px var(--cyan-dim);transform:translateY(-2px)}
.reroll-btn:focus-visible{outline:2px solid var(--cyan);outline-offset:2px}
```

- [ ] **Step 4: 視覺驗證（截 PNG 給 user）**

啟動前端 + backend（見專案 HANDOFF），用 Playwright 開頁面、查一個職涯、截圖顯示「換一批」按鈕，點擊後課程卡內容改變再截一張。**把 PNG 給 user 看，等 user 說「對」。**

- [ ] **Step 5: Commit（user 說「對」後才執行）**

```bash
git add frontend/index.html frontend/app.js frontend/style.css
git commit -m "feat(frontend): 換一批推薦按鈕"
```

---

### Task 5: 前端底部 CTA 導向問答

**Files:**
- Modify: `frontend/index.html`（#results 底部加 CTA）
- Modify: `frontend/app.js`（綁定 → setMode("qa")）
- Modify: `frontend/style.css`（CTA 樣式）

- [ ] **Step 1: index.html 加 CTA**

在 Task 4 的 `.results-actions` 區塊內、按鈕之後加入一段 CTA：

```html
        <div class="results-actions">
          <button id="reroll-btn" class="reroll-btn" type="button">換一批推薦 🔄</button>
        </div>
        <p class="to-qa-cta">想深入比較這些課？<a id="to-qa-link" href="#" role="button">切到問答模式問我 →</a></p>
```

- [ ] **Step 2: app.js 綁定 CTA**

在 Task 4 的 reroll 綁定後新增（此時 `setMode` 已於檔案後段定義，但事件綁定在執行期才觸發，順序無妨；為保險將綁定放在 `setMode` 定義之後，例如第 255 行 `chipQa.addEventListener` 之後）：

```javascript
const toQaLink = document.getElementById("to-qa-link");
toQaLink.addEventListener("click", (e)=>{ e.preventDefault(); setMode("qa"); });
```

- [ ] **Step 3: style.css 加樣式**

檔尾加入：

```css
.to-qa-cta{text-align:center;margin-top:14px;font-size:13.5px;color:var(--text-dim)}
.to-qa-cta a{color:var(--cyan);text-decoration:none;border-bottom:1px solid transparent;transition:border-color .15s;margin-left:4px}
.to-qa-cta a:hover{border-bottom-color:var(--cyan)}
```

- [ ] **Step 4: 視覺驗證（截 PNG 給 user）**

Playwright 截圖顯示底部 CTA；點擊後確認切換到問答模式（問答輸入框出現、mode chip 切換）。**PNG 給 user 看，等「對」。**

- [ ] **Step 5: Commit（user 說「對」後）**

```bash
git add frontend/index.html frontend/app.js frontend/style.css
git commit -m "feat(frontend): 推薦結果底部 CTA 導向問答模式"
```

---

### Task 6: E2E 整合驗證（real Playwright，5x consecutive）

**Files:**
- Create: `tests/e2e/recommend-diversity.spec.ts`（或專案既有 e2e 目錄慣例）

- [ ] **Step 1: 寫 E2E spec**

涵蓋完整 user flow（real backend + full store）：
1. 選職涯 → 送出 → 顯示 10 門課，記錄課程名稱集合 S1。
2. 點「換一批推薦」→ 等新結果 → 記錄集合 S2。
3. 斷言 `S1 !== S2`（跨次輪替生效）且兩批皆非空（仍相關）。
4. 點底部 CTA → 斷言切換到問答模式（`#qa-composer` 可見、`#mode-qa` 顯示）。

使用 role/data-attr locator（禁座標、禁 desktop-only selector）；多階段用 `test.step()` 分段。

- [ ] **Step 2: 跑 5 次連續確認 0 flake**

Run: 連續執行 5 次，每次都需 pass（記錄每 run pass/total）。
Expected: 5/5 consecutive green（符合本 session e2e mandate）。

- [ ] **Step 3: 跨裝置/尺寸**

以 mobile(390) / tablet(768) / desktop viewport 各跑一次，確認「換一批」按鈕與 CTA 在各尺寸可見可點。

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/recommend-diversity.spec.ts
git commit -m "test(e2e): 推薦多樣性換一批 + 導問答 整合測試"
```

---

## Self-Review

**Spec coverage**
- 設計 A 擴大池 40 → Task 2 Step 1 ✓；定錨+seed 抽樣 → Task 1 ✓；seed 介面（request 選用/response 回傳/未帶亂數）→ Task 3 ✓；保品質定錨 → Task 1 實作 ✓；可重現 → Task 1 測試 ✓；dry-run 小池相容 → Task 1 `test_small_pool_returned_as_is` + Task 2 Step 4 ✓。
- 設計 B1 換一批 → Task 4 ✓；B2 底部 CTA → Task 5 ✓；不碰課程卡 ✓。
- 測試策略（單元、既有相容、live 雙 seed、Playwright、跨裝置）→ Task 1/2/3/6 ✓。
- 非目標（Phase 2 歷史去重）→ 未納入，正確 ✓。

**Placeholder scan**：無 TBD/TODO；每個 code 步驟皆含完整程式碼 ✓。

**Type consistency**：`sample_candidates(candidates, seed, anchor_count, sample_size)`、`build_recommendation_instrumented(..., seed=0)`、`result["seed"]`、`RecommendRequest.seed`、`RecommendResponse.seed` 命名一致 ✓。
