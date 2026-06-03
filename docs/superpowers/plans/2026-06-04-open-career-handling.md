# 清單外職涯處理 Implementation Plan

> REQUIRED SUB-SKILL: subagent-driven-development。Steps 用 `- [ ]`。
> 規範：TDD red→green；單元測試 mock Gemini client（不打真 API）；前端改動截 PNG 給 user 確認後才 commit；live AC 用 Playwright（full store 就緒後）。

**Goal:** 清單外職涯（如清潔工）不卡死：LLM 推導可轉移能力→真實檢索推薦（誠實 notice），真查無則導向問答。

**AC:** 見 spec `2026-06-04-open-career-handling-design.md` AC1-AC6。

---

### Task 1: `derive_skills_for_career`（recommend.py，TDD mock）

**Files:** Modify `backend/recommend.py`；Test `tests/backend/test_open_career.py`（新建）

- [ ] **Step 1 失敗測試**（mock client.models.generate_content 回傳 JSON 字串）
```python
# tests/backend/test_open_career.py
from unittest.mock import MagicMock
from backend.recommend import derive_skills_for_career


def _client_returning(text):
    c = MagicMock()
    c.models.generate_content.return_value = MagicMock(text=text)
    return c


def test_derive_skills_parses_list():
    c = _client_returning('["衛生管理","公共衛生","基礎管理","人際溝通","職場安全"]')
    skills = derive_skills_for_career(c, "清潔工")
    assert "公共衛生" in skills and len(skills) >= 3


def test_derive_skills_none_for_garbage():
    c = _client_returning('[]')   # LLM 判定非真實職涯 → 空陣列
    assert derive_skills_for_career(c, "asdfqwer") is None
```

- [ ] **Step 2 跑測試 RED**：`.venv/bin/python -m pytest tests/backend/test_open_career.py -q` → ImportError。

- [ ] **Step 3 實作**（recommend.py，放在 stage1_retrieve 之前）
```python
def derive_skills_for_career(client: genai.Client, career: str) -> list[str] | None:
    """為清單外職涯用 LLM 推導『可轉移／學術可教』技能關鍵字。

    回 None 表示輸入非真實職涯（無法推導）。
    """
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
```

- [ ] **Step 4 GREEN**：跑測試 → 2 passed。再跑 `.venv/bin/python -m pytest tests/ -q` 全綠。

- [ ] **Step 5 Commit**：`git add backend/recommend.py tests/backend/test_open_career.py && git commit -m "feat(recommend): derive_skills_for_career 清單外職涯技能推導"`

---

### Task 2: skills 參數 + stage1 誠實回空（recommend.py，TDD）

**Files:** Modify `backend/recommend.py`；Test 同上檔案追加

- [ ] **Step 1 失敗測試**（build_recommendation_instrumented 接受 skills override；mock stage1/stage2）
```python
def test_build_uses_injected_skills(monkeypatch):
    import backend.recommend as R
    captured = {}
    def fake_stage1(client, store, career, skills):
        captured["skills"] = skills
        return [{"course_id":"000211012","course_name":"X","relevance":"r"}]
    def fake_stage2(client, career, skills, candidates):
        from backend.recommend import _Stage2Output, _Groups
        return _Stage2Output(groups=_Groups(core=[],supporting=[],extended=[]))
    monkeypatch.setattr(R,"stage1_retrieve",fake_stage1)
    monkeypatch.setattr(R,"stage2_group",fake_stage2)
    monkeypatch.setattr(R,"load_courses_meta",lambda:{})
    monkeypatch.setattr(R,"load_careers",lambda:{})
    R.build_recommendation_instrumented(None,"store","清潔工",seed=0,skills=["公共衛生"])
    assert captured["skills"] == ["公共衛生"]
```

- [ ] **Step 2 RED** → 跑測試失敗（skills 參數不存在 / career 不在 careers 會先丟錯）。

- [ ] **Step 3 實作**：
  - `build_recommendation_instrumented(client, store_name, career, seed=0, skills=None)`：若 `skills` 給定則用之，否則 `careers[career]["skills"]`（career 不在且無 skills→ ValueError 照舊）。`build_recommendation` 同步加 `skills=None`。
  - `stage1_retrieve` prompt 末加一行：`"若知識庫中沒有任何課程與這些技能真正相關，請回傳空陣列 []，不要硬湊不相關的課。"`

- [ ] **Step 4 GREEN**：測試 + 全測試綠。

- [ ] **Step 5 Commit**：`git add -u && git commit -m "feat(recommend): build_recommendation 支援 skills override + stage1 誠實回空"`

---

### Task 3: models + main.py 分支（notice / no_match）

**Files:** Modify `backend/models.py`、`backend/main.py`；Test `tests/backend/test_open_career_api.py`

- [ ] **Step 1 失敗測試**（用 FastAPI TestClient + monkeypatch pipeline）
```python
# tests/backend/test_open_career_api.py
from fastapi.testclient import TestClient
import backend.main as M

def test_unknown_career_no_match(monkeypatch):
    monkeypatch.setattr(M,"build_recommendation_instrumented",lambda *a,**k:(_ for _ in ()).throw(AssertionError("不該被呼叫")))
    monkeypatch.setattr(M,"derive_skills_for_career",lambda c,career:None)
    client=TestClient(M.app)
    r=client.post("/recommend",json={"career":"asdfqwer"})
    assert r.status_code==200
    assert r.json().get("no_match") is True

def test_unknown_career_with_skills_recommends(monkeypatch):
    monkeypatch.setattr(M,"derive_skills_for_career",lambda c,career:["公共衛生"])
    monkeypatch.setattr(M,"build_recommendation_instrumented",
        lambda client,store,career,seed,skills=None:({"career":career,"groups":{"core":[],"supporting":[],"extended":[]},"latency_ms":1,"seed":seed,"notice":f"政大沒有直接對應「{career}」的課程，以下為可轉移能力課程："},1))
    client=TestClient(M.app)
    r=client.post("/recommend",json={"career":"清潔工"})
    assert r.status_code==200
    assert "可轉移能力" in (r.json().get("notice") or "")
```

- [ ] **Step 2 RED**。

- [ ] **Step 3 實作**
  - `models.py`：`RecommendResponse` 加 `notice: str | None = None`；新增
    ```python
    class NoMatchResponse(BaseModel):
        career: str
        no_match: bool = True
        message: str
    ```
  - `main.py` `/recommend`：移除 `response_model`（改回傳 dict，支援兩種形狀），邏輯：
    ```python
    careers = load_careers()
    seed = req.seed if req.seed is not None else random.randrange(1_000_000)
    if req.career in careers:
        skills = None  # 用靜態
    else:
        skills = derive_skills_for_career(_client, req.career)
        if not skills:
            return {"career": req.career, "no_match": True,
                    "message": f"目前沒有找到對應「{req.career}」的課程，你可以用問答模式問我相關方向。"}
    result=None; stage1_count=0; error=None
    try:
        result, stage1_count = build_recommendation_instrumented(_client,_STORE_NAME,req.career,seed,skills=skills)
    except Exception as e:
        error=e
    # 清單外但檢索空 → no_match（非錯誤）
    if not error and skills is not None and result and not any(result["groups"].values()):
        return {"career": req.career, "no_match": True,
                "message": f"政大課程偏學術，目前沒有找到與「{req.career}」相關的課程，建議用問答模式探索。"}
    background_tasks.add_task(_background_log_and_judge, req.career, result, stage1_count, error)
    if error: raise HTTPException(503, str(error))
    # 清單外有結果 → 補 notice
    if skills is not None and result is not None:
        result.setdefault("notice", f"政大沒有直接對應「{req.career}」的課程，但以下課程能培養相關的可轉移能力：")
    return result
    ```
  - import：`from backend.recommend import ..., derive_skills_for_career`。

- [ ] **Step 4 GREEN**：測試 + 全測試綠。

- [ ] **Step 5 Commit**：`git add backend/models.py backend/main.py tests/backend/test_open_career_api.py && git commit -m "feat(recommend): /recommend 清單外職涯分支（notice/no_match，不再 400）"`

---

### Task 4: 前端（放寬送出 + notice + no_match 導問答）— 由 Opus 自行實作（UI 不派 sonnet）

**Files:** `frontend/app.js`、`frontend/index.html`、`frontend/style.css`

- [ ] **Step 1**：放寬送出——輸入框 `input` 事件時，非空就 `searchBtn.disabled=false`；送出改用「selectedCareer || 輸入框文字」。
- [ ] **Step 2**：`renderResults` 開頭，若 `data.notice` → 在 `#groups` 上方插一段 `.result-notice` 誠實說明條。
- [ ] **Step 3**：`fetchRecommendation` 收到 `data.no_match` → 呼叫新 `showNoMatch(career,message)`：隱藏 results、顯示一張訊息卡（message + 「用問答模式問我 →」按鈕 → `setMode("qa")` 並把 `career` 帶入 `qaInput`）。
- [ ] **Step 4**：CSS `.result-notice`（柔和提示條）、`.no-match-card`（置中訊息卡 + CTA 按鈕），融入 navy 風格。
- [ ] **Step 5 視覺驗證**：Playwright 注入 notice 樣本 + no_match 樣本各截一張 PNG，cold-read 後給 user 確認 → 通過才 commit。

---

### Task 5: Playwright AC E2E（full store 就緒後跑）

**Files:** `tests/e2e/open-career.spec.ts`（或 MCP 互動驗證）

- [ ] AC5：打「清潔工」可送出（按鈕不死鎖）。
- [ ] AC3：清潔工 → 顯示 notice + 真實課程卡（斷言卡片有課綱連結、course_id 存在於 courses_meta）。
- [ ] AC4：亂打 → no_match 訊息 + 一鍵切問答。
- [ ] AC1：50 種內職涯仍正常出三組卡（防回歸）。
- [ ] AC6：抽查課程卡 course_id 為真。
- [ ] 5x 連續綠；mobile/tablet/desktop 各一次。

## Self-Review
- 涵蓋 spec AC1-AC6（Task5）；no_match 回 200 不卡前端；課程卡來自真實檢索（AC6 斷言）；前端放寬送出（AC5）。
- 無 placeholder；型別 `skills`/`notice`/`no_match`/`NoMatchResponse` 一致。
