# Backend API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Prerequisite:** Plan 1 (Ingestion) must be complete. `backend/courses_meta.json` must exist.
>
> **Parallel failure handling:** If multiple independent test files fail, use `superpowers:dispatching-parallel-agents`.

**Goal:** Build a FastAPI backend with a `POST /recommend` endpoint that performs two-stage Gemini retrieval and returns grouped course recommendations with syllabus links.

**Architecture:** FastAPI loads `career_skills.json` + `courses_meta.json` at startup. `POST /recommend` → Stage 1 Gemini File Search → Stage 2 Gemini structured output → metadata join → return JSON.

**Tech Stack:** Python 3.11+, FastAPI>=0.115, uvicorn, google-genai>=1.0, pydantic>=2.0, python-dotenv, pytest, httpx (for test client)

---

## File Map

| File | Responsibility |
|------|---------------|
| `backend/main.py` | FastAPI app, CORS, startup, `/recommend` route |
| `backend/recommend.py` | Two-stage Gemini pipeline + metadata join |
| `backend/models.py` | Pydantic request/response models |
| `backend/career_skills.json` | 50 careers with skill lists (static data) |
| `backend/courses_meta.json` | 2877 course metadata (from ingestion) |
| `backend/Dockerfile` | Production container |
| `backend/requirements.txt` | Python dependencies |
| `tests/backend/test_recommend.py` | Unit tests for recommend logic |
| `tests/backend/test_api.py` | FastAPI endpoint tests (TestClient) |
| `tests/backend/__init__.py` | Package marker |

---

## Task 1: Backend Setup + Models

**Files:**
- Create: `backend/requirements.txt`
- Create: `backend/models.py`
- Create: `tests/backend/__init__.py`

- [ ] **Step 1: Create `backend/requirements.txt`**

```
fastapi>=0.115
uvicorn[standard]>=0.30
google-genai>=1.0
python-dotenv>=1.0
pydantic>=2.7
pytest>=8.0
pytest-asyncio>=0.24
httpx>=0.27
```

- [ ] **Step 2: Install**

```bash
pip install -r backend/requirements.txt
```

- [ ] **Step 3: Write failing test for models**

```python
# tests/backend/test_recommend.py
import pytest
from backend.models import RecommendRequest, CourseCard, RecommendResponse

def test_recommend_request_valid():
    r = RecommendRequest(career="產品經理(PM)")
    assert r.career == "產品經理(PM)"

def test_recommend_request_empty_career_fails():
    with pytest.raises(Exception):
        RecommendRequest(career="")

def test_course_card_has_required_fields():
    card = CourseCard(
        course_id="000211012",
        name="政治學",
        department="政治系",
        teacher="蔡中民",
        credits=3.0,
        reason="培養分析能力",
        syllabus_url="https://x.com/a",
    )
    assert card.course_id == "000211012"
    assert card.syllabus_url == "https://x.com/a"
```

- [ ] **Step 4: Run to verify fail**

```bash
pytest tests/backend/test_recommend.py::test_recommend_request_valid -v
```

Expected: `ImportError` — `backend.models` not defined.

- [ ] **Step 5: Implement `backend/models.py`**

```python
# backend/models.py
from pydantic import BaseModel, field_validator


class RecommendRequest(BaseModel):
    career: str

    @field_validator("career")
    @classmethod
    def career_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("career must not be empty")
        return v.strip()


class CourseCard(BaseModel):
    course_id: str
    name: str
    department: str
    teacher: str
    credits: float
    reason: str
    syllabus_url: str


class CourseGroups(BaseModel):
    core: list[CourseCard]
    supporting: list[CourseCard]
    extended: list[CourseCard]


class RecommendResponse(BaseModel):
    career: str
    groups: CourseGroups
    latency_ms: int
```

- [ ] **Step 6: Run tests — verify pass**

```bash
pytest tests/backend/test_recommend.py -v
```

Expected: 3 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/requirements.txt backend/models.py tests/backend/__init__.py tests/backend/test_recommend.py
git commit -m "feat(backend): Pydantic models for recommend API"
```

---

## Task 2: `career_skills.json` — 50 Careers

**Files:**
- Create: `backend/career_skills.json`

- [ ] **Step 1: Create `backend/career_skills.json`**

```json
{
  "產品經理(PM)": {
    "label": "產品經理(PM)",
    "skills": ["產品規劃", "用戶研究", "需求分析", "數據分析", "跨部門溝通", "專案管理", "product sense", "market research"]
  },
  "資料科學家": {
    "label": "資料科學家",
    "skills": ["機器學習", "統計分析", "Python", "資料視覺化", "特徵工程", "模型評估", "SQL", "深度學習"]
  },
  "軟體工程師": {
    "label": "軟體工程師",
    "skills": ["程式設計", "演算法", "系統設計", "版本控制", "測試驅動開發", "API設計", "資料結構", "DevOps"]
  },
  "UX/UI設計師": {
    "label": "UX/UI設計師",
    "skills": ["用戶研究", "原型設計", "資訊架構", "視覺設計", "可用性測試", "互動設計", "使用者訪談", "wireframe"]
  },
  "行銷企劃": {
    "label": "行銷企劃",
    "skills": ["市場分析", "品牌策略", "內容行銷", "活動策劃", "數位行銷", "消費者洞察", "廣告投放", "行銷漏斗"]
  },
  "品牌管理": {
    "label": "品牌管理",
    "skills": ["品牌策略", "品牌識別", "市場定位", "消費者行為", "品牌溝通", "競爭分析", "視覺設計", "故事行銷"]
  },
  "財務分析師": {
    "label": "財務分析師",
    "skills": ["財務報表分析", "估值模型", "風險評估", "Excel建模", "投資分析", "財務規劃", "DCF", "統計"]
  },
  "投資銀行家": {
    "label": "投資銀行家",
    "skills": ["併購分析", "資本市場", "財務建模", "盡職調查", "股權估值", "債務融資", "商業談判", "簡報技巧"]
  },
  "會計師": {
    "label": "會計師",
    "skills": ["財務會計", "審計", "稅務規劃", "成本會計", "財報編製", "內部控制", "會計準則", "ERP系統"]
  },
  "公關專員": {
    "label": "公關專員",
    "skills": ["媒體關係", "危機公關", "新聞稿撰寫", "品牌形象", "社群媒體", "活動規劃", "議題管理", "口語表達"]
  },
  "管理顧問": {
    "label": "管理顧問",
    "skills": ["問題結構化", "商業分析", "策略規劃", "簡報製作", "客戶管理", "專案管理", "資料分析", "議題樹"]
  },
  "創業家": {
    "label": "創業家",
    "skills": ["商業模式設計", "精實創業", "募資", "市場驗證", "領導力", "產品開發", "財務規劃", "用戶訪談"]
  },
  "廣告創意": {
    "label": "廣告創意",
    "skills": ["創意發想", "文案寫作", "視覺傳達", "品牌溝通", "廣告策略", "媒體企劃", "說故事", "洞察消費者"]
  },
  "新聞記者": {
    "label": "新聞記者",
    "skills": ["新聞採訪", "事實查核", "文字撰寫", "批判性思考", "資訊素養", "媒體倫理", "調查報導", "多媒體製作"]
  },
  "社群媒體經理": {
    "label": "社群媒體經理",
    "skills": ["社群策略", "內容創作", "社群分析", "KOL合作", "廣告投放", "危機處理", "社群互動", "平台演算法"]
  },
  "人力資源": {
    "label": "人力資源",
    "skills": ["招募甄選", "培訓發展", "績效管理", "薪酬福利", "組織發展", "勞動法規", "員工關係", "HRBP"]
  },
  "律師": {
    "label": "律師",
    "skills": ["法律研究", "法律文書撰寫", "案件分析", "談判", "法庭辯護", "合約審閱", "法律倫理", "批判性思考"]
  },
  "外交官": {
    "label": "外交官",
    "skills": ["國際關係", "外交談判", "跨文化溝通", "外語能力", "政策分析", "多邊外交", "外交禮儀", "國際法"]
  },
  "公務員": {
    "label": "公務員",
    "skills": ["行政管理", "政策分析", "公共事務", "法規理解", "文書處理", "預算規劃", "公民服務", "研究報告"]
  },
  "NGO工作者": {
    "label": "NGO工作者",
    "skills": ["倡議策略", "計畫撰寫", "募款", "社區組織", "影響力評估", "利害關係人溝通", "社會創新", "志工管理"]
  },
  "教師": {
    "label": "教師",
    "skills": ["課程設計", "教學方法", "班級經營", "學習評量", "學生輔導", "教育心理", "差異化教學", "教育研究"]
  },
  "心理師": {
    "label": "心理師",
    "skills": ["諮商技術", "心理評估", "個案概念化", "心理治療理論", "危機介入", "心理衛生", "倫理判斷", "研究方法"]
  },
  "社工師": {
    "label": "社工師",
    "skills": ["個案管理", "社區工作", "社會政策", "資源連結", "危機介入", "方案規劃", "倡議", "評估"]
  },
  "學術研究員": {
    "label": "學術研究員",
    "skills": ["研究設計", "文獻回顧", "量化分析", "質性研究", "學術寫作", "統計方法", "同儕評審", "研究倫理"]
  },
  "業務銷售": {
    "label": "業務銷售",
    "skills": ["客戶開發", "銷售技巧", "談判協商", "客戶關係管理", "市場分析", "簡報技巧", "目標達成", "CRM"]
  },
  "供應鏈管理": {
    "label": "供應鏈管理",
    "skills": ["物流管理", "採購策略", "庫存控制", "供應商管理", "需求預測", "ERP系統", "作業管理", "精益生產"]
  },
  "電商營運": {
    "label": "電商營運",
    "skills": ["電商平台管理", "數位行銷", "用戶體驗", "數據分析", "庫存管理", "客服管理", "轉換率優化", "SEO/SEM"]
  },
  "遊戲策劃": {
    "label": "遊戲策劃",
    "skills": ["遊戲設計", "關卡設計", "用戶體驗", "數值設計", "競品分析", "玩家心理", "敘事設計", "資料分析"]
  },
  "內容創作者": {
    "label": "內容創作者",
    "skills": ["內容策略", "影片製作", "腳本寫作", "社群經營", "SEO", "受眾分析", "品牌合作", "數位工具"]
  },
  "風險管理師": {
    "label": "風險管理師",
    "skills": ["風險識別", "風險評估", "風險控制", "法規遵循", "量化風險模型", "壓力測試", "保險規劃", "內部稽核"]
  },
  "精算師": {
    "label": "精算師",
    "skills": ["精算數學", "統計模型", "保險定價", "準備金計算", "風險評估", "財務數學", "法規遵循", "資料分析"]
  },
  "不動產顧問": {
    "label": "不動產顧問",
    "skills": ["市場分析", "資產估值", "財務分析", "法規了解", "談判技巧", "開發評估", "投資分析", "客戶關係"]
  },
  "ESG/永續長": {
    "label": "ESG/永續長",
    "skills": ["ESG框架", "永續報告", "碳盤查", "利害關係人溝通", "政策倡議", "循環經濟", "供應鏈永續", "數據分析"]
  },
  "資安工程師": {
    "label": "資安工程師",
    "skills": ["網路安全", "滲透測試", "弱點評估", "密碼學", "事件回應", "SIEM", "雲端安全", "合規管理"]
  },
  "AI工程師": {
    "label": "AI工程師",
    "skills": ["深度學習", "大語言模型", "MLOps", "Python", "PyTorch/TensorFlow", "提示工程", "向量資料庫", "系統設計"]
  },
  "金融科技": {
    "label": "金融科技",
    "skills": ["支付系統", "區塊鏈", "監管科技", "API設計", "風控模型", "用戶體驗", "金融法規", "數據分析"]
  },
  "影視製作": {
    "label": "影視製作",
    "skills": ["劇本創作", "鏡頭語言", "剪輯", "導演技術", "製片管理", "視覺效果", "配樂", "故事結構"]
  },
  "公共政策": {
    "label": "公共政策",
    "skills": ["政策分析", "成本效益分析", "利害關係人分析", "法規研究", "政治經濟", "量化分析", "政策評估", "公共行政"]
  },
  "都市規劃師": {
    "label": "都市規劃師",
    "skills": ["土地使用規劃", "都市設計", "GIS", "環境評估", "交通規劃", "社區參與", "法規了解", "永續發展"]
  },
  "醫療管理": {
    "label": "醫療管理",
    "skills": ["醫療政策", "健康照護管理", "財務管理", "品質管理", "醫療資訊系統", "法規遵循", "組織管理", "健康經濟"]
  },
  "國際貿易": {
    "label": "國際貿易",
    "skills": ["貿易法規", "國際物流", "貿易融資", "關稅制度", "合約談判", "供應鏈", "外語能力", "市場開拓"]
  },
  "跨文化溝通": {
    "label": "跨文化溝通",
    "skills": ["跨文化理解", "外語能力", "翻譯技巧", "全球思維", "外交禮儀", "衝突解決", "國際合作", "文化研究"]
  },
  "翻譯口譯": {
    "label": "翻譯口譯",
    "skills": ["雙語能力", "翻譯技巧", "口譯技術", "語言學", "文化轉換", "術語管理", "快速理解", "資訊整理"]
  },
  "圖書館資訊": {
    "label": "圖書館資訊",
    "skills": ["資訊組織", "資料庫管理", "知識管理", "用戶服務", "數位典藏", "資訊檢索", "後設資料", "研究支援"]
  },
  "廣播電視主持": {
    "label": "廣播電視主持",
    "skills": ["口語表達", "採訪技巧", "臨場反應", "稿件撰寫", "媒體素養", "聲音技巧", "節目製作", "觀眾互動"]
  },
  "媒體購買": {
    "label": "媒體購買",
    "skills": ["媒體規劃", "廣告投放", "數據分析", "談判技巧", "受眾分析", "ROI評估", "程序化廣告", "預算管理"]
  },
  "數位行銷": {
    "label": "數位行銷",
    "skills": ["SEO/SEM", "社群廣告", "內容行銷", "電子郵件行銷", "數據分析", "轉換率優化", "Google Analytics", "行銷自動化"]
  },
  "統計分析師": {
    "label": "統計分析師",
    "skills": ["統計推論", "迴歸分析", "實驗設計", "R/Python", "資料視覺化", "貝氏統計", "時間序列", "調查設計"]
  },
  "生技製藥": {
    "label": "生技製藥",
    "skills": ["分子生物學", "藥物開發", "臨床試驗", "法規科學", "生物統計", "研究設計", "智慧財產", "市場准入"]
  },
  "觀光旅遊管理": {
    "label": "觀光旅遊管理",
    "skills": ["旅遊規劃", "款待業管理", "文化觀光", "永續旅遊", "活動策劃", "客戶服務", "旅遊行銷", "危機管理"]
  }
}
```

- [ ] **Step 2: Write test for career lookup**

```python
# append to tests/backend/test_recommend.py

import json
from pathlib import Path

def test_career_skills_json_has_50_careers():
    data = json.loads(Path("backend/career_skills.json").read_text(encoding="utf-8"))
    assert len(data) >= 50

def test_career_skills_json_pm_has_skills():
    data = json.loads(Path("backend/career_skills.json").read_text(encoding="utf-8"))
    pm = data["產品經理(PM)"]
    assert "skills" in pm
    assert len(pm["skills"]) >= 5
```

- [ ] **Step 3: Run test — verify pass**

```bash
pytest tests/backend/test_recommend.py::test_career_skills_json_has_50_careers tests/backend/test_recommend.py::test_career_skills_json_pm_has_skills -v
```

Expected: 2 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/career_skills.json
git commit -m "feat(backend): career_skills.json with 50 career profiles"
```

---

## Task 3: `recommend.py` — Core Logic (TDD)

**Files:**
- Create: `backend/recommend.py`
- Expand: `tests/backend/test_recommend.py`

- [ ] **Step 1: Write failing tests for recommend logic**

```python
# append to tests/backend/test_recommend.py
from backend.recommend import deduplicate_by_prefix, join_metadata, load_careers, load_courses_meta

def test_deduplicate_by_prefix_keeps_first():
    courses = [
        {"course_id": "000211012", "reason": "a"},
        {"course_id": "000211022", "reason": "b"},  # same 6-digit prefix — should be dropped
        {"course_id": "000216001", "reason": "c"},  # different prefix
    ]
    result = deduplicate_by_prefix(courses)
    assert len(result) == 2
    assert result[0]["course_id"] == "000211012"
    assert result[1]["course_id"] == "000216001"

def test_deduplicate_by_prefix_empty_list():
    assert deduplicate_by_prefix([]) == []

def test_join_metadata_enriches_cards():
    raw = [{"course_id": "000211012", "reason": "培養分析能力"}]
    meta = {
        "000211012": {
            "name": "政治學", "department": "政治系", "teacher": "蔡中民",
            "credits": 3.0, "syllabus_url": "https://x.com/a", "source": "syllabus"
        }
    }
    result = join_metadata(raw, meta)
    assert len(result) == 1
    assert result[0]["name"] == "政治學"
    assert result[0]["syllabus_url"] == "https://x.com/a"
    assert result[0]["reason"] == "培養分析能力"

def test_join_metadata_skips_missing_course_id():
    raw = [
        {"course_id": "000211012", "reason": "a"},
        {"course_id": "NOTEXIST99", "reason": "b"},
    ]
    meta = {
        "000211012": {
            "name": "政治學", "department": "政治系", "teacher": "蔡中民",
            "credits": 3.0, "syllabus_url": "https://x.com/a", "source": "syllabus"
        }
    }
    result = join_metadata(raw, meta)
    assert len(result) == 1

def test_load_careers_returns_dict():
    careers = load_careers()
    assert "產品經理(PM)" in careers
    assert "skills" in careers["產品經理(PM)"]

def test_load_courses_meta_returns_dict_or_empty():
    # May return empty if courses_meta.json not yet generated
    meta = load_courses_meta()
    assert isinstance(meta, dict)
```

- [ ] **Step 2: Run tests to verify fail**

```bash
pytest tests/backend/test_recommend.py::test_deduplicate_by_prefix_keeps_first -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement `backend/recommend.py`**

```python
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
```

- [ ] **Step 4: Run all recommend tests — verify pass**

```bash
pytest tests/backend/test_recommend.py -v
```

Expected: All tests PASS (the Gemini integration tests are excluded — only unit tests run).

- [ ] **Step 5: Commit**

```bash
git add backend/recommend.py tests/backend/test_recommend.py
git commit -m "feat(backend): two-stage Gemini recommend pipeline with metadata join"
```

---

## Task 4: FastAPI App + API Tests

**Files:**
- Create: `backend/main.py`
- Create: `tests/backend/test_api.py`

- [ ] **Step 1: Write failing API tests**

```python
# tests/backend/test_api.py
import pytest
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

@pytest.fixture
def client():
    # NOTE: When Plan 4 (Logging) is implemented, update this patch to:
    # patch("backend.main.build_recommendation_instrumented")
    # and have mock return (result_dict, stage1_count) tuple instead.
    with patch("backend.main.build_recommendation") as mock_build:
        mock_build.return_value = {
            "career": "產品經理(PM)",
            "groups": {
                "core": [{"course_id": "000211012", "name": "政治學", "department": "政治系",
                          "teacher": "蔡中民", "credits": 3.0,
                          "reason": "培養分析能力", "syllabus_url": "https://x.com/a"}],
                "supporting": [],
                "extended": [],
            },
            "latency_ms": 1200,
        }
        from backend.main import app
        yield TestClient(app), mock_build


def test_recommend_valid_career(client):
    tc, mock_fn = client
    resp = tc.post("/recommend", json={"career": "產品經理(PM)"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["career"] == "產品經理(PM)"
    assert "groups" in data
    assert "core" in data["groups"]
    mock_fn.assert_called_once()


def test_recommend_invalid_career_returns_400(client):
    tc, _ = client
    resp = tc.post("/recommend", json={"career": "不存在的職業XYZ"})
    assert resp.status_code == 400


def test_recommend_empty_career_returns_422(client):
    tc, _ = client
    resp = tc.post("/recommend", json={"career": ""})
    assert resp.status_code == 422


def test_health_check(client):
    tc, _ = client
    resp = tc.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 2: Run tests to verify fail**

```bash
pytest tests/backend/test_api.py -v
```

Expected: `ImportError` — `backend.main` not defined.

- [ ] **Step 3: Implement `backend/main.py`**

```python
# backend/main.py
from __future__ import annotations
import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from google import genai

from backend.models import RecommendRequest, RecommendResponse
from backend.recommend import build_recommendation, load_careers

load_dotenv()
_GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
_STORE_NAME = os.environ["FILE_SEARCH_STORE_NAME"]
_ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")

app = FastAPI(title="NCCU Course Recommender")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[_ALLOWED_ORIGIN] if _ALLOWED_ORIGIN != "*" else ["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type"],
)

_client = genai.Client(api_key=_GEMINI_API_KEY)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/recommend", response_model=RecommendResponse)
def recommend(req: RecommendRequest):
    careers = load_careers()
    if req.career not in careers:
        raise HTTPException(status_code=400, detail=f"Unknown career: {req.career}")
    try:
        result = build_recommendation(_client, _STORE_NAME, req.career)
        return result
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
```

- [ ] **Step 4: Run API tests — verify pass**

```bash
pytest tests/backend/test_api.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Run server locally to smoke-test**

```bash
cd backend && uvicorn main:app --reload --port 8000
```

Then in another terminal:
```bash
curl -s -X POST http://localhost:8000/health | python -m json.tool
```

Expected: `{"status": "ok"}`

- [ ] **Step 6: Commit**

```bash
git add backend/main.py tests/backend/test_api.py
git commit -m "feat(backend): FastAPI app with /recommend endpoint and CORS"
```

---

## Task 5: Dockerfile + railway.toml

**Files:**
- Create: `backend/Dockerfile`
- Create: `railway.toml`

- [ ] **Step 1: Create `backend/Dockerfile`**

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Create `railway.toml`**

```toml
[build]
builder = "dockerfile"
dockerfilePath = "backend/Dockerfile"

[deploy]
startCommand = "uvicorn main:app --host 0.0.0.0 --port $PORT"
healthcheckPath = "/health"
healthcheckTimeout = 30
```

- [ ] **Step 3: Build Docker image locally to verify**

```bash
cd backend
docker build -t nccu-backend .
```

Expected: Build completes without error.

- [ ] **Step 4: Commit**

```bash
git add backend/Dockerfile railway.toml
git commit -m "chore(backend): Dockerfile and railway.toml for deployment"
```

---

## Task 6: Full Backend Verification

- [ ] **Step 1: Run all backend tests**

```bash
pytest tests/backend/ -v
```

Expected: All tests PASS.

- [ ] **Step 2: Integration smoke test (requires real Gemini + store)**

Ensure `.env` has `FILE_SEARCH_STORE_NAME` populated (from ingestion).

```bash
cd backend && uvicorn main:app --port 8000 &
sleep 2
curl -s -X POST http://localhost:8000/recommend \
  -H "Content-Type: application/json" \
  -d '{"career": "資料科學家"}' | python -m json.tool | head -30
```

Expected: JSON with `groups.core`, `groups.supporting`, `groups.extended`, each with at least 1 course.

- [ ] **Step 3: Verify `superpowers:verification-before-completion`**

```bash
pytest tests/ -v --ignore=tests/ingestion
```

Expected: All backend tests PASS. No errors or warnings.

- [ ] **Step 4: Use `superpowers:requesting-code-review` on backend/**

Dispatch code reviewer subagent to review `backend/recommend.py` and `backend/main.py`.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat(backend): complete recommend API with tests"
```
