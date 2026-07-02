# 系所過濾（Department Filter）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓問答指定系所（「歷史系」）或學制（「大學部」）時只回該系/該學制的課，靠「vector store 補系所 attribute + 後端受控檢索硬篩」，未指定時行為不變。

**Architecture:** 建庫端一次性把 535 種髒系所字串用官方名冊 + LLM 映射成 `dept_canonical`/`college`，degree_level 用規則推導，backfill 到既有 store 檔案（不重建、不重 embedding）。查詢端在既有 condense 後加「抽系所/學制 → 三層正規化 → 後端 `vector_stores.search` 帶 `filters` 硬篩 → 注入 context 生成」；抽不到/對不上退回現有純語意路徑。

**Tech Stack:** Python 3.11 + FastAPI、openai SDK（Vector Stores + Responses API，`gpt-5.4-mini`）、asyncpg、rapidfuzz（新增）、pytest。

## Global Constraints

- 生成/抽取模型一律 `OPENAI_MODEL`（預設 `gpt-5.4-mini`），透過既有 `_client`（見 `backend/qa.py`）。勿引入 Gemini（`tests/backend/test_no_gemini.py` 守門）。
- vector store id 讀 `OPENAI_VECTOR_STORE_ID`（生產 `vs_6a26fe2c36b8819182550837ed5fce7d`）。
- OpenAI attributes 限制：≤16 keys、key ≤64 字、value ≤512 字（string/number/bool）。既有 `course_id`、`syllabus_url` 不可移除。
- 檢索/生成走 async 主 loop，勿 `asyncio.run`/`to_thread` 開新 loop（見設計決策 #22，`test_recommend_loop_fix.py`）。
- 未指定系所時**行為完全不變**（回歸既有 file_search 路徑）。抽不到/對不上/filter 後 0 筆→退回純語意，**絕不誤過濾成 0 筆**。
- canonical 值域來自 `docs/superpowers/specs/2026-07-02-nccu-official-dept-vocab.md`。
- commit 結尾：`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。

---

## 檔案結構

**新增（backend runtime）：**
- `backend/dept_canonical.json` — controlled vocabulary：`{colleges: {學院: [系所...]}, special_units: [...], dept_to_college: {系所: 學院}, dept_aliases: {別名: 系所}, degree_aliases: {別名: 學制}, ge_course_id_prefixes: [...]}`
- `backend/dept_vocab.py` — 載入 `dept_canonical.json`；規則函式 `infer_degree_level`、`strip_grade_tokens`；提供 canonical 清單與別名表。
- `backend/dept_query.py` — 查詢端：`normalize_department`、`normalize_degree`（三層）、`extract_slots`（LLM）、`build_dept_filter`。
- `backend/dept_mapping.json` — 產出物：`{髒系所字串: {dept_canonical, college, confidence}}`（Task 2 產生、committed）。

**新增（scripts，gitignored 但 force-add 保留）：**
- `scripts/build_dept_mapping.py` — LLM 一次性映射 535 髒值 → `dept_mapping.json`。
- `scripts/audit_dept_mapping.py` — 覆蓋率/桶異常/低信心統計。
- `scripts/backfill_dept_attributes.py` — 對既有 store 檔案 POST attributes。

**修改：**
- `backend/qa.py` — 在 condense 後加系所感知檢索分支。
- `backend/requirements.txt` — 加 `rapidfuzz`。

**測試：**
- `tests/backend/test_dept_vocab.py`、`test_dept_query.py`、`test_dept_filter.py`、`test_qa_dept_filter.py`
- `tests/backend/test_build_dept_mapping.py`、`test_backfill_dept_attributes.py`

**並行分工（dispatch parallel agents，上限 3-5）：**
- Task 1 為共同基礎，先完成。
- 之後 A 線（建庫端）Task 2→3→4 與 B 線（查詢端）Task 5→6 可平行。
- Task 7（qa.py 整合）需 A 線 backfill 完 + B 線完成後才做。
- Task 8（e2e）、Task 9（review+audit）收尾。

---

## Task 1: Controlled vocabulary + 規則函式（共同基礎）

**Files:**
- Create: `backend/dept_canonical.json`
- Create: `backend/dept_vocab.py`
- Test: `tests/backend/test_dept_vocab.py`

**Interfaces:**
- Produces:
  - `load_vocab() -> dict`（載入 json，lru_cache）
  - `CANONICAL_DEPTS: set[str]`、`DEPT_TO_COLLEGE: dict[str,str]`、`DEPT_ALIASES: dict[str,str]`、`DEGREE_ALIASES: dict[str,str]`
  - `strip_grade_tokens(s: str) -> str`（砍年級/班別/學制/數字得詞幹）
  - `infer_degree_level(dirty: str, course_id: str) -> str`（回傳 學士/碩士/博士/碩博/通識/其他）

- [ ] **Step 1: 建 `backend/dept_canonical.json`**

依 `docs/superpowers/specs/2026-07-02-nccu-official-dept-vocab.md` §1-§2 填入。結構：

```json
{
  "colleges": {
    "文學院": ["中國文學系","歷史學系","哲學系","圖書資訊與檔案學研究所","宗教研究所","臺灣史研究所","臺灣文學研究所"],
    "理學院": ["應用數學系","心理學系","神經科學研究所","應用物理研究所","電子物理學士學位學程"],
    "社會科學學院": ["政治學系","社會學系","財政學系","公共行政學系","地政學系","經濟學系","民族學系","國家發展研究所","勞動研究所","社會工作研究所"],
    "法學院": ["法律學系","法律科際整合研究所"],
    "商學院": ["國際經營與貿易學系","金融學系","會計學系","統計學系","企業管理學系","資訊管理學系","財務管理學系","風險管理與保險學系","科技管理與智慧財產研究所"],
    "外國語文學院": ["英國語文學系","阿拉伯語文學系","斯拉夫語文學系","日本語文學系","韓國語文學系","土耳其語文學系","歐洲語文學系","東南亞語文學系","語言學研究所"],
    "傳播學院": ["新聞學系","廣告學系","廣播電視學系","傳播學士學位學程"],
    "國際事務學院": ["外交學系","東亞研究所","俄羅斯研究所"],
    "教育學院": ["教育學系","幼兒教育研究所","教育行政與政策研究所"],
    "資訊學院": ["資訊科學系","資訊安全碩士學位學程","數位內容學位學程","人工智慧應用學士學位學程"],
    "創新國際學院": ["創新國際學院"]
  },
  "special_units": ["體育室","通識教育中心","外文中心","華語文教學中心","師資培育中心","AI中心"],
  "dept_aliases": {
    "歷史系":"歷史學系","歷史":"歷史學系","中文系":"中國文學系","中文":"中國文學系",
    "法律系":"法律學系","法律":"法律學系","英文系":"英國語文學系","英文":"英國語文學系",
    "資管":"資訊管理學系","資訊管理系":"資訊管理學系","資科":"資訊科學系","資訊系":"資訊科學系",
    "日文系":"日本語文學系","韓文系":"韓國語文學系","地政":"地政學系","政治系":"政治學系"
  },
  "college_aliases": {"文院":"文學院","文苑":"文學院","理院":"理學院","商院":"商學院","法院":"法學院","社科院":"社會科學學院","傳院":"傳播學院","國務院":"國際事務學院","教院":"教育學院"},
  "degree_aliases": {"大學部":"學士","大學":"學士","本科":"學士","學士班":"學士","研究所":"研究所","碩士班":"碩士","碩班":"碩士","碩士":"碩士","博士班":"博士","博班":"博士","博士":"博士"},
  "ge_course_id_prefixes": ["041","044","045"]
}
```

> `ge_course_id_prefixes` 為初值；Task 1 Step 4 會用資料校準（見下）。`dept_aliases`/`college_aliases` 為常見別名快路，非窮舉——未命中靠 Task 5 的 rapidfuzz 層。

- [ ] **Step 2: 寫失敗測試 `tests/backend/test_dept_vocab.py`**

```python
from backend.dept_vocab import (
    load_vocab, CANONICAL_DEPTS, DEPT_TO_COLLEGE,
    strip_grade_tokens, infer_degree_level,
)

def test_canonical_and_college_mapping():
    assert "歷史學系" in CANONICAL_DEPTS
    assert DEPT_TO_COLLEGE["歷史學系"] == "文學院"
    assert DEPT_TO_COLLEGE["資訊管理學系"] == "商學院"
    assert DEPT_TO_COLLEGE["資訊科學系"] == "資訊學院"

def test_strip_grade_tokens():
    assert strip_grade_tokens("歷史一") == "歷史"
    assert strip_grade_tokens("歷史碩一歷史博一歷史碩二歷史博二") == "歷史"
    assert strip_grade_tokens("中文三甲中文三乙") == "中文"
    assert strip_grade_tokens("歷史系") == "歷史系"  # 系字保留，靠 alias 對映

def test_infer_degree_level_by_rule():
    assert infer_degree_level("歷史一", "103xxxxxx") == "學士"
    assert infer_degree_level("歷史碩一歷史碩二", "153xxxxxx") == "碩士"
    assert infer_degree_level("歷史博一", "153xxxxxx") == "博士"
    assert infer_degree_level("歷史碩一歷史博一歷史碩二歷史博二", "153xxxxxx") == "碩博"

def test_infer_degree_level_ge_by_prefix():
    # 041/044 前綴的「歷史系」通識課
    assert infer_degree_level("歷史系", "041xxxxxx") == "通識"
```

- [ ] **Step 3: 跑測試確認 RED**

Run: `cd /Users/albertpeng/Desktop/claude_project/NCCU-POC && ALLOWED_ORIGIN=* python -m pytest tests/backend/test_dept_vocab.py -v`
Expected: FAIL（module `backend.dept_vocab` not found）

- [ ] **Step 4: 校準 `ge_course_id_prefixes` 並實作 `backend/dept_vocab.py`**

先用資料找出「跨多系共用（=通識/共同）的 course_id 前綴」：

```bash
cd /Users/albertpeng/Desktop/claude_project/NCCU-POC && python3 - <<'PY'
import json, collections, re
meta=json.load(open("backend/courses_meta.json"))
def base(d):
    m=re.match(r'^(.+?)(?:[一二三四甲乙丙丁]|碩|博|系|所|學程|\d)', d); return (m.group(1) if m else d) or d
pref=collections.defaultdict(set)
for cid,v in meta.items(): pref[cid[:3]].add(base(v.get("department","")))
# 跨 >=6 個不同詞幹的前綴視為通識/共同
ge=sorted(p for p,b in pref.items() if len(b)>=6)
print("GE/共同前綴：", ge)
PY
```

把輸出的前綴填進 `dept_canonical.json` 的 `ge_course_id_prefixes`（取代初值）。然後實作：

```python
# backend/dept_vocab.py
from __future__ import annotations
import json, re
from functools import lru_cache
from pathlib import Path

_VOCAB_PATH = Path(__file__).parent / "dept_canonical.json"

@lru_cache(maxsize=1)
def load_vocab() -> dict:
    return json.loads(_VOCAB_PATH.read_text(encoding="utf-8"))

_V = load_vocab()
CANONICAL_DEPTS: set[str] = {d for depts in _V["colleges"].values() for d in depts} | set(_V["special_units"])
DEPT_TO_COLLEGE: dict[str, str] = {d: c for c, depts in _V["colleges"].items() for d in depts}
DEPT_ALIASES: dict[str, str] = _V["dept_aliases"]
COLLEGE_ALIASES: dict[str, str] = _V["college_aliases"]
DEGREE_ALIASES: dict[str, str] = _V["degree_aliases"]
GE_PREFIXES: set[str] = set(_V["ge_course_id_prefixes"])

_GRADE_TOKEN = re.compile(r'(?:[一二三四五六七八九十甲乙丙丁]|碩|博|在職|專班|學程|組|系|所|\d)+')

def strip_grade_tokens(s: str) -> str:
    """砍年級/班別/學制/數字得系名詞幹；重複段取第一個詞幹。"""
    # 取字串開頭到第一個 grade token 前的中文詞幹
    m = re.match(r'^([一-鿿]+?)(?:[一二三四五六七八九十甲乙丙丁碩博]|\d)', s)
    stem = m.group(1) if m else _GRADE_TOKEN.sub('', s)
    return stem or s

def infer_degree_level(dirty: str, course_id: str) -> str:
    if course_id[:3] in GE_PREFIXES:
        return "通識"
    has_m, has_d = "碩" in dirty, "博" in dirty
    if has_m and has_d:
        return "碩博"
    if has_m:
        return "碩士"
    if has_d:
        return "博士"
    if re.search(r'[一二三四]', dirty) or dirty.endswith("系"):
        return "學士"
    return "其他"
```

- [ ] **Step 5: 跑測試確認 GREEN**

Run: `cd /Users/albertpeng/Desktop/claude_project/NCCU-POC && ALLOWED_ORIGIN=* python -m pytest tests/backend/test_dept_vocab.py -v`
Expected: PASS（5 passed）。若 `test_infer_degree_level_ge_by_prefix` 失敗，確認 041 在校準後的 `ge_course_id_prefixes` 內。

- [ ] **Step 6: Commit**

```bash
git add backend/dept_canonical.json backend/dept_vocab.py tests/backend/test_dept_vocab.py
git commit -m "feat(dept): controlled vocabulary + 系所/學制規則函式"
```

---

## Task 2: LLM 一次性映射 535 髒值 → dept_mapping.json（建庫 A 線）

**Files:**
- Create: `scripts/build_dept_mapping.py`
- Create（產出物）: `backend/dept_mapping.json`
- Test: `tests/backend/test_build_dept_mapping.py`

**Interfaces:**
- Consumes: `backend/dept_vocab.py`（CANONICAL_DEPTS、DEPT_TO_COLLEGE）、`backend/courses_meta.json`
- Produces: `distinct_dirty_values(meta) -> list[str]`、`build_mapping_prompt(dirty_batch, canonical_list) -> str`、`parse_mapping_response(text) -> dict`、`dept_mapping.json`

- [ ] **Step 1: 寫失敗測試 `tests/backend/test_build_dept_mapping.py`**（測純函式，不打 LLM）

```python
from scripts.build_dept_mapping import distinct_dirty_values, parse_mapping_response

def test_distinct_dirty_values_dedups():
    meta = {"1": {"department": "歷史一"}, "2": {"department": "歷史一"}, "3": {"department": "中文系"}}
    assert sorted(distinct_dirty_values(meta)) == ["中文系", "歷史一"]

def test_parse_mapping_response_json():
    text = '```json\n{"歷史一":{"dept_canonical":"歷史學系","confidence":"high"}}\n```'
    out = parse_mapping_response(text)
    assert out["歷史一"]["dept_canonical"] == "歷史學系"
```

- [ ] **Step 2: 跑測試確認 RED**

Run: `cd /Users/albertpeng/Desktop/claude_project/NCCU-POC && ALLOWED_ORIGIN=* python -m pytest tests/backend/test_build_dept_mapping.py -v`
Expected: FAIL（module not found）

- [ ] **Step 3: 實作 `scripts/build_dept_mapping.py`**

```python
# scripts/build_dept_mapping.py
"""一次性：把 courses_meta 的 535 種髒 department 值用 LLM 映射到 canonical 系所。
用法：OPENAI_API_KEY=... python scripts/build_dept_mapping.py
輸出：backend/dept_mapping.json  {dirty: {dept_canonical, college, confidence}}"""
from __future__ import annotations
import json, os, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.dept_vocab import CANONICAL_DEPTS, DEPT_TO_COLLEGE, strip_grade_tokens

ROOT = Path(__file__).resolve().parent.parent
META = json.loads((ROOT / "backend/courses_meta.json").read_text(encoding="utf-8"))
OUT = ROOT / "backend/dept_mapping.json"

def distinct_dirty_values(meta: dict) -> list[str]:
    return sorted({v.get("department", "") for v in meta.values() if v.get("department")})

def build_mapping_prompt(dirty_batch: list[str], canonical_list: list[str]) -> str:
    return (
        "你是政大課程資料正規化助手。下面每一個是課程資料裡『開課系所』的髒寫法，"
        "請把每一個映射到『合法系所清單』中最正確的一個 canonical 系所名。"
        "**只能用清單內的值，不得自創**。無法判斷就填 \"其他\" 並 confidence=\"low\"。\n\n"
        f"合法系所清單：{json.dumps(canonical_list, ensure_ascii=False)}\n\n"
        f"待映射：{json.dumps(dirty_batch, ensure_ascii=False)}\n\n"
        '回傳 JSON：{"髒值":{"dept_canonical":"清單內值","confidence":"high|medium|low"}}'
    )

def parse_mapping_response(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    return json.loads(text)

def main() -> None:
    from openai import OpenAI
    client = OpenAI()
    model = os.environ.get("OPENAI_MODEL", "gpt-5.4-mini")
    dirty = distinct_dirty_values(META)
    canonical = sorted(CANONICAL_DEPTS) + ["其他"]
    result: dict = {}
    BATCH = 60
    for i in range(0, len(dirty), BATCH):
        batch = dirty[i:i + BATCH]
        resp = client.responses.create(
            model=model,
            input=build_mapping_prompt(batch, canonical),
        )
        parsed = parse_mapping_response(resp.output_text)
        for k, v in parsed.items():
            dc = v.get("dept_canonical", "其他")
            if dc not in CANONICAL_DEPTS:
                dc, v["confidence"] = "其他", "low"
            result[k] = {"dept_canonical": dc, "college": DEPT_TO_COLLEGE.get(dc, ""), "confidence": v.get("confidence", "low")}
        print(f"  mapped {i+len(batch)}/{len(dirty)}", file=sys.stderr)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {OUT} ({len(result)} entries)")

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑測試確認 GREEN**

Run: `cd /Users/albertpeng/Desktop/claude_project/NCCU-POC && ALLOWED_ORIGIN=* python -m pytest tests/backend/test_build_dept_mapping.py -v`
Expected: PASS

- [ ] **Step 5: 離線跑映射（真 LLM，一次性）**

Run: `cd /Users/albertpeng/Desktop/claude_project/NCCU-POC && OPENAI_API_KEY=$OPENAI_API_KEY python scripts/build_dept_mapping.py`
Expected: 印 `wrote .../dept_mapping.json (535 entries)`。開檔抽看 `歷史一/歷史系/中文碩一...` 對映合理。

- [ ] **Step 6: Commit**

```bash
git add scripts/build_dept_mapping.py backend/dept_mapping.json tests/backend/test_build_dept_mapping.py
git commit -m "feat(dept): LLM 一次性映射 535 髒系所值 → dept_mapping.json"
```

---

## Task 3: 稽核對照表（覆蓋率/桶異常/低信心）+ 人工核 6 歧義（建庫 A 線）

**Files:**
- Create: `scripts/audit_dept_mapping.py`
- Modify（人工核後）: `backend/dept_mapping.json`

**Interfaces:**
- Consumes: `backend/dept_mapping.json`、`backend/courses_meta.json`、`backend/dept_vocab.py`

- [ ] **Step 1: 實作 `scripts/audit_dept_mapping.py`**

```python
# scripts/audit_dept_mapping.py
"""稽核 dept_mapping.json：覆蓋率、每 canonical 桶課數、低信心/其他 清單。"""
from __future__ import annotations
import json, collections, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
ROOT = Path(__file__).resolve().parent.parent
meta = json.loads((ROOT / "backend/courses_meta.json").read_text(encoding="utf-8"))
mapping = json.loads((ROOT / "backend/dept_mapping.json").read_text(encoding="utf-8"))

total = len(meta); mapped = other = 0
bucket = collections.Counter(); low = []
for v in meta.values():
    d = v.get("department", "")
    m = mapping.get(d)
    if not m or m["dept_canonical"] == "其他":
        other += 1
    else:
        mapped += 1
        bucket[m["dept_canonical"]] += 1
    if m and m.get("confidence") == "low":
        low.append(d)
print(f"覆蓋：{mapped}/{total}（{mapped/total*100:.1f}%）；落『其他』：{other}")
print(f"\n桶大小前 15：")
for dept, n in bucket.most_common(15):
    print(f"  {dept:<16} {n}")
print(f"\n異常肥大桶（>150 課，疑誤併）：{[d for d,n in bucket.items() if n>150]}")
print(f"\n低信心髒值（{len(set(low))}）：{sorted(set(low))}")
```

- [ ] **Step 2: 跑稽核**

Run: `cd /Users/albertpeng/Desktop/claude_project/NCCU-POC && python scripts/audit_dept_mapping.py`
Expected: 覆蓋率應 ≥98%。記下低信心清單與異常桶。

- [ ] **Step 3: 人工核 spec §3 的 6 歧義詞幹 + 低信心項**

依 `2026-07-02-nccu-official-dept-vocab.md` §3，逐一檢查 `dept_mapping.json` 裡這些髒值的對映（`資博產/資博學`、`國關通`、`國教碩`、`原碩專`、`生科學程`、`數位`* 相關），對照 course_id 修正。**這一步必須人工，不可略。** 改完存檔。

- [ ] **Step 4: Commit**

```bash
git add scripts/audit_dept_mapping.py backend/dept_mapping.json
git commit -m "feat(dept): 對照表稽核腳本 + 人工核 6 歧義詞幹"
```

---

## Task 4: Backfill store attributes（建庫 A 線）

**Files:**
- Create: `scripts/backfill_dept_attributes.py`
- Test: `tests/backend/test_backfill_dept_attributes.py`

**Interfaces:**
- Consumes: `backend/dept_mapping.json`、`backend/courses_meta.json`、`backend/dept_vocab.py`（infer_degree_level）
- Produces: `build_attributes(course_id, rec, mapping) -> dict`（回傳要 POST 的完整 attributes，含既有 course_id/syllabus_url）

- [ ] **Step 1: 寫失敗測試 `tests/backend/test_backfill_dept_attributes.py`**

```python
from scripts.backfill_dept_attributes import build_attributes

def test_build_attributes_preserves_existing_and_adds_facets():
    rec = {"course_id": "103123001", "department": "歷史一", "syllabus_url": "http://x"}
    mapping = {"歷史一": {"dept_canonical": "歷史學系", "college": "文學院"}}
    attrs = build_attributes("103123001", rec, mapping)
    assert attrs["course_id"] == "103123001"      # 既有保留
    assert attrs["syllabus_url"] == "http://x"
    assert attrs["dept_canonical"] == "歷史學系"    # 新增
    assert attrs["college"] == "文學院"
    assert attrs["degree_level"] == "學士"          # 規則推導
    assert len(attrs) <= 16

def test_build_attributes_unmapped_falls_to_other():
    rec = {"course_id": "999000001", "department": "不存在系", "syllabus_url": ""}
    attrs = build_attributes("999000001", rec, {})
    assert attrs["dept_canonical"] == "其他"
```

- [ ] **Step 2: 跑測試確認 RED**

Run: `cd /Users/albertpeng/Desktop/claude_project/NCCU-POC && ALLOWED_ORIGIN=* python -m pytest tests/backend/test_backfill_dept_attributes.py -v`
Expected: FAIL

- [ ] **Step 3: 實作 `scripts/backfill_dept_attributes.py`**

```python
# scripts/backfill_dept_attributes.py
"""對既有 vector store 檔案補 dept_canonical/college/degree_level attribute（不重傳檔案）。
用法：OPENAI_API_KEY=... OPENAI_VECTOR_STORE_ID=... python scripts/backfill_dept_attributes.py [--dry-run]"""
from __future__ import annotations
import json, os, sys, asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.dept_vocab import infer_degree_level

ROOT = Path(__file__).resolve().parent.parent
META = json.loads((ROOT / "backend/courses_meta.json").read_text(encoding="utf-8"))
MAPPING = json.loads((ROOT / "backend/dept_mapping.json").read_text(encoding="utf-8"))

def build_attributes(course_id: str, rec: dict, mapping: dict) -> dict:
    m = mapping.get(rec.get("department", ""), {})
    dc = m.get("dept_canonical", "其他")
    return {
        "course_id": course_id,
        "syllabus_url": rec.get("syllabus_url", ""),
        "dept_canonical": dc,
        "college": m.get("college", ""),
        "degree_level": infer_degree_level(rec.get("department", ""), course_id),
    }

async def main() -> None:
    from openai import AsyncOpenAI
    dry = "--dry-run" in sys.argv
    vs = os.environ["OPENAI_VECTOR_STORE_ID"]
    client = AsyncOpenAI()
    # 建 course_id -> file_id 對照（attributes.course_id 已存在）
    file_by_cid: dict[str, str] = {}
    after = None
    while True:
        page = await client.vector_stores.files.list(vector_store_id=vs, limit=100, after=after)
        for f in page.data:
            cid = (f.attributes or {}).get("course_id")
            if cid:
                file_by_cid[cid] = f.id
        if not page.has_more:
            break
        after = page.data[-1].id
    print(f"store 檔案 {len(file_by_cid)}；meta {len(META)}", file=sys.stderr)
    sem = asyncio.Semaphore(16); done = 0
    async def one(cid: str, rec: dict):
        nonlocal done
        fid = file_by_cid.get(cid)
        if not fid:
            return
        attrs = build_attributes(cid, rec, MAPPING)
        if not dry:
            async with sem:
                await client.vector_stores.files.update(vector_store_id=vs, file_id=fid, attributes=attrs)
        done += 1
        if done % 200 == 0:
            print(f"  {done}", file=sys.stderr)
    await asyncio.gather(*(one(cid, rec) for cid, rec in META.items()))
    print(f"{'DRY ' if dry else ''}updated {done} files")

if __name__ == "__main__":
    asyncio.run(main())
```

> 註：SDK 更新 attributes 的方法名以實測為準（`vector_stores.files.update(...)`；若該版本無此法，改 `client.vector_stores.files.create` 帶同 file_id + attributes，或 REST `POST /vector_stores/{vs}/files/{file_id}`）。先 `--dry-run` 驗證 file_by_cid 對得起來。

- [ ] **Step 4: 跑測試確認 GREEN**

Run: `cd /Users/albertpeng/Desktop/claude_project/NCCU-POC && ALLOWED_ORIGIN=* python -m pytest tests/backend/test_backfill_dept_attributes.py -v`
Expected: PASS

- [ ] **Step 5: Dry-run 再正式 backfill（真 store，一次性）**

```bash
cd /Users/albertpeng/Desktop/claude_project/NCCU-POC
OPENAI_API_KEY=$OPENAI_API_KEY OPENAI_VECTOR_STORE_ID=$OPENAI_VECTOR_STORE_ID python scripts/backfill_dept_attributes.py --dry-run
# 確認 "store 檔案 ~2718；meta 2718" 對得起來後，正式跑：
OPENAI_API_KEY=$OPENAI_API_KEY OPENAI_VECTOR_STORE_ID=$OPENAI_VECTOR_STORE_ID python scripts/backfill_dept_attributes.py
```
Expected: `updated ~2718 files`。

- [ ] **Step 6: 驗證 store 已可過濾（真 API 探針）**

```bash
cd /Users/albertpeng/Desktop/claude_project/NCCU-POC && OPENAI_API_KEY=$OPENAI_API_KEY OPENAI_VECTOR_STORE_ID=$OPENAI_VECTOR_STORE_ID python3 - <<'PY'
import os
from openai import OpenAI
c=OpenAI(); vs=os.environ["OPENAI_VECTOR_STORE_ID"]
r=c.vector_stores.search(vector_store_id=vs, query="歷史",
    filters={"type":"eq","key":"dept_canonical","value":"歷史學系"}, max_num_results=10)
print("命中：", len(r.data))
for d in r.data[:10]:
    print("  ", d.attributes.get("dept_canonical"), d.attributes.get("degree_level"), d.filename)
PY
```
Expected: 回傳的 `dept_canonical` **全部是「歷史學系」**（證明硬過濾生效）。

- [ ] **Step 7: Commit**

```bash
git add scripts/backfill_dept_attributes.py tests/backend/test_backfill_dept_attributes.py
git commit -m "feat(dept): backfill store 檔案 dept/college/degree attributes"
```

---

## Task 5: 查詢端三層正規化（B 線）

**Files:**
- Create: `backend/dept_query.py`（本 Task 只做 normalize 部分）
- Modify: `backend/requirements.txt`（加 `rapidfuzz`）
- Test: `tests/backend/test_dept_query.py`

**Interfaces:**
- Consumes: `backend/dept_vocab.py`
- Produces:
  - `normalize_department(raw: str | None) -> str | None`（回 canonical 系所或 None）
  - `normalize_college(raw: str | None) -> str | None`
  - `normalize_degree(raw: str | None) -> str | None`

- [ ] **Step 1: 加依賴**

在 `backend/requirements.txt` 末加一行：`rapidfuzz`。Run: `pip install rapidfuzz`。

- [ ] **Step 2: 寫失敗測試 `tests/backend/test_dept_query.py`**

```python
from backend.dept_query import normalize_department, normalize_college, normalize_degree

def test_alias_exact():
    assert normalize_department("歷史系") == "歷史學系"
    assert normalize_department("資管") == "資訊管理學系"

def test_already_canonical():
    assert normalize_department("歷史學系") == "歷史學系"

def test_fuzzy_typo():
    assert normalize_college("文苑") == "文學院"      # 錯字 → 最近鄰
    assert normalize_college("文院") == "文學院"      # 別名

def test_degree_alias():
    assert normalize_degree("大學部") == "學士"
    assert normalize_degree("研究所") == "研究所"
    assert normalize_degree("碩士班") == "碩士"

def test_garbage_returns_none():
    assert normalize_department("asdfqwer") is None
    assert normalize_department(None) is None
```

- [ ] **Step 3: 跑測試確認 RED**

Run: `cd /Users/albertpeng/Desktop/claude_project/NCCU-POC && ALLOWED_ORIGIN=* python -m pytest tests/backend/test_dept_query.py -v`
Expected: FAIL

- [ ] **Step 4: 實作 normalize（三層：alias/exact → rapidfuzz → None）**

```python
# backend/dept_query.py
from __future__ import annotations
from rapidfuzz import process, fuzz
from backend.dept_vocab import (
    CANONICAL_DEPTS, DEPT_ALIASES, COLLEGE_ALIASES, DEGREE_ALIASES,
    strip_grade_tokens,
)

_COLLEGES = set(COLLEGE_ALIASES.values()) | {"文學院","理學院","社會科學學院","法學院","商學院","外國語文學院","傳播學院","國際事務學院","教育學院","資訊學院","創新國際學院"}
_FUZZ_CUTOFF = 82  # 字面相似度門檻；低於此視為對不上

def _match(raw: str | None, alias: dict[str, str], canonical: set[str]) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    if raw in canonical:            # 層1a：已是標準值
        return raw
    if raw in alias:                # 層1b：別名快路
        return alias[raw]
    stem = strip_grade_tokens(raw)
    if stem in alias:
        return alias[stem]
    # 層2：rapidfuzz 最近鄰（治錯字），比對 canonical + alias keys
    pool = list(canonical) + list(alias.keys())
    hit = process.extractOne(raw, pool, scorer=fuzz.WRatio, score_cutoff=_FUZZ_CUTOFF)
    if hit:
        val = hit[0]
        return alias.get(val, val)
    return None                     # 層3：對不上 → None（不過濾）

def normalize_department(raw: str | None) -> str | None:
    return _match(raw, DEPT_ALIASES, CANONICAL_DEPTS)

def normalize_college(raw: str | None) -> str | None:
    return _match(raw, COLLEGE_ALIASES, _COLLEGES)

def normalize_degree(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    if raw in DEGREE_ALIASES:
        return DEGREE_ALIASES[raw]
    hit = process.extractOne(raw, list(DEGREE_ALIASES.keys()), scorer=fuzz.WRatio, score_cutoff=_FUZZ_CUTOFF)
    return DEGREE_ALIASES[hit[0]] if hit else None
```

> 設計註：v1 第二層用 rapidfuzz 字面距離（治「文苑」類錯字），零額外 API 呼叫。spec 提到的 embedding 語意最近鄰列為**擴充點**——若 e2e 發現語意變體（非錯字）漏接率高再加（precompute canonical embeddings + 查詢時 1 次 embedding）。

- [ ] **Step 5: 跑測試確認 GREEN**

Run: `cd /Users/albertpeng/Desktop/claude_project/NCCU-POC && ALLOWED_ORIGIN=* python -m pytest tests/backend/test_dept_query.py -v`
Expected: PASS（若 `文苑` 未過，微調 `_FUZZ_CUTOFF`；記錄最終值）

- [ ] **Step 6: Commit**

```bash
git add backend/dept_query.py backend/requirements.txt tests/backend/test_dept_query.py
git commit -m "feat(dept): 查詢端三層正規化（alias+rapidfuzz）"
```

---

## Task 6: 查詢端 slot 抽取 + filter 組裝（B 線）

**Files:**
- Modify: `backend/dept_query.py`（加 `extract_slots`、`build_dept_filter`）
- Test: `tests/backend/test_dept_filter.py`

**Interfaces:**
- Consumes: `_client`/`OPENAI_MODEL`（沿用 `backend/qa.py` 匯出的 async client）、Task 5 的 normalize 函式
- Produces:
  - `async extract_slots(query: str) -> dict`（回 `{"department": str|None, "college": str|None, "degree_level": str|None}`，值已正規化）
  - `build_dept_filter(slots: dict) -> dict | None`（組 OpenAI filters；空→None）

- [ ] **Step 1: 寫失敗測試 `tests/backend/test_dept_filter.py`**（filter 純函式）

```python
from backend.dept_query import build_dept_filter

def test_dept_only():
    f = build_dept_filter({"department": "歷史學系", "college": None, "degree_level": None})
    assert f == {"type": "eq", "key": "dept_canonical", "value": "歷史學系"}

def test_dept_and_degree_masters_expands_in():
    f = build_dept_filter({"department": "歷史學系", "college": None, "degree_level": "碩士"})
    assert f["type"] == "and"
    assert {"type": "eq", "key": "dept_canonical", "value": "歷史學系"} in f["filters"]
    assert {"type": "in", "key": "degree_level", "value": ["碩士", "碩博"]} in f["filters"]

def test_degree_only_undergrad():
    f = build_dept_filter({"department": None, "college": None, "degree_level": "學士"})
    assert f == {"type": "eq", "key": "degree_level", "value": "學士"}

def test_graduate_expands_all():
    f = build_dept_filter({"department": None, "college": None, "degree_level": "研究所"})
    assert f == {"type": "in", "key": "degree_level", "value": ["碩士", "博士", "碩博"]}

def test_empty_returns_none():
    assert build_dept_filter({"department": None, "college": None, "degree_level": None}) is None
```

- [ ] **Step 2: 跑測試確認 RED**

Run: `cd /Users/albertpeng/Desktop/claude_project/NCCU-POC && ALLOWED_ORIGIN=* python -m pytest tests/backend/test_dept_filter.py -v`
Expected: FAIL

- [ ] **Step 3: 實作 `build_dept_filter` + `extract_slots`**（append 進 `backend/dept_query.py`）

```python
# --- append to backend/dept_query.py ---
import json
from backend.dept_vocab import CANONICAL_DEPTS

_DEGREE_IN = {
    "碩士": ["碩士", "碩博"],
    "博士": ["博士", "碩博"],
    "研究所": ["碩士", "博士", "碩博"],
}

def build_dept_filter(slots: dict) -> dict | None:
    clauses: list[dict] = []
    if slots.get("department"):
        clauses.append({"type": "eq", "key": "dept_canonical", "value": slots["department"]})
    if slots.get("college"):
        clauses.append({"type": "eq", "key": "college", "value": slots["college"]})
    dl = slots.get("degree_level")
    if dl:
        if dl in _DEGREE_IN:
            clauses.append({"type": "in", "key": "degree_level", "value": _DEGREE_IN[dl]})
        else:  # 學士/通識 精確
            clauses.append({"type": "eq", "key": "degree_level", "value": dl})
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"type": "and", "filters": clauses}

_EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "department": {"type": ["string", "null"], "description": "使用者指定的系所名稱原文，未指定填 null"},
        "college": {"type": ["string", "null"], "description": "使用者指定的學院名稱原文，未指定填 null"},
        "degree_level": {"type": ["string", "null"], "description": "大學部/學士/碩士/博士/研究所 等原文，未指定填 null"},
    },
    "required": ["department", "college", "degree_level"],
    "additionalProperties": False,
}

async def extract_slots(query: str) -> dict:
    """從（已 condense 的）query 抽系所/學院/學制原文，再正規化到 canonical。抽不到全 None。"""
    from backend.qa import _client, OPENAI_MODEL  # 沿用既有 async client
    try:
        resp = await _client.responses.create(
            model=OPENAI_MODEL,
            input=f"從這句課程查詢抽出使用者明確指定的系所/學院/學制，未提到就填 null：\n{query}",
            text={"format": {"type": "json_schema", "name": "slots", "schema": _EXTRACT_SCHEMA, "strict": True}},
        )
        raw = json.loads(resp.output_text)
    except Exception:
        return {"department": None, "college": None, "degree_level": None}
    return {
        "department": normalize_department(raw.get("department")),
        "college": normalize_college(raw.get("college")),
        "degree_level": normalize_degree(raw.get("degree_level")),
    }
```

> 註：`responses.create` 的 structured-output 參數名以專案現有 `condense_question`（`backend/qa.py`）的寫法為準（它已用 `responses.parse`/structured output）——照抄同一種呼叫法，避免 SDK 版本參數不符。

- [ ] **Step 4: 跑測試確認 GREEN**

Run: `cd /Users/albertpeng/Desktop/claude_project/NCCU-POC && ALLOWED_ORIGIN=* python -m pytest tests/backend/test_dept_filter.py -v`
Expected: PASS

- [ ] **Step 5: extract_slots 整合小測（真 LLM，1-2 例）**

```bash
cd /Users/albertpeng/Desktop/claude_project/NCCU-POC && OPENAI_API_KEY=$OPENAI_API_KEY ALLOWED_ORIGIN=* python3 - <<'PY'
import asyncio
from backend.dept_query import extract_slots
print(asyncio.run(extract_slots("推薦給我 10 門歷史系的課")))
print(asyncio.run(extract_slots("我只想要大學部輕鬆一點的課")))
print(asyncio.run(extract_slots("有什麼有趣的課")))
PY
```
Expected: 第一句 `department=歷史學系`；第二句 `degree_level=學士`；第三句全 None。

- [ ] **Step 6: Commit**

```bash
git add backend/dept_query.py tests/backend/test_dept_filter.py
git commit -m "feat(dept): slot 抽取 + filter 組裝（含碩博 in 展開）"
```

---

## Task 7: 整合進 qa.py（系所感知受控檢索分支）

**Files:**
- Modify: `backend/qa.py`
- Test: `tests/backend/test_qa_dept_filter.py`

**Interfaces:**
- Consumes: `backend/dept_query.py`（extract_slots、build_dept_filter）、既有 `condense_question`、`course_ids_from_search_results`、`_client`
- Produces: 修改後的問答 pipeline——有 dept filter 時走「後端 search + 注入 context 生成」，否則走現狀。

- [ ] **Step 1: 先讀懂現有 stream_answer / finalize / condense 的介面**

Run: `cd /Users/albertpeng/Desktop/claude_project/NCCU-POC && ALLOWED_ORIGIN=* python -m pytest tests/backend -q`（先確認現有測試全綠，記基準數字）
Read: `backend/qa.py` 的 `condense_question`、`stream_answer`、`finalize_qa_answer`、`_SYSTEM_INSTRUCTION`、`course_ids_from_search_results`（`backend/retrieval_openai.py`）。

- [ ] **Step 2: 寫失敗測試 `tests/backend/test_qa_dept_filter.py`**

```python
import asyncio
from backend import dept_query

def test_dept_query_produces_filter_for_history(monkeypatch):
    async def fake_extract(q):
        return {"department": "歷史學系", "college": None, "degree_level": None}
    monkeypatch.setattr(dept_query, "extract_slots", fake_extract)
    slots = asyncio.run(dept_query.extract_slots("歷史系的課"))
    f = dept_query.build_dept_filter(slots)
    assert f == {"type": "eq", "key": "dept_canonical", "value": "歷史學系"}

def test_vague_query_no_filter(monkeypatch):
    async def fake_extract(q):
        return {"department": None, "college": None, "degree_level": None}
    monkeypatch.setattr(dept_query, "extract_slots", fake_extract)
    slots = asyncio.run(dept_query.extract_slots("有趣的課"))
    assert dept_query.build_dept_filter(slots) is None
```

- [ ] **Step 3: 跑測試確認 RED**

Run: `cd /Users/albertpeng/Desktop/claude_project/NCCU-POC && ALLOWED_ORIGIN=* python -m pytest tests/backend/test_qa_dept_filter.py -v`
Expected: FAIL（import/behaviour）

- [ ] **Step 4: 在 qa.py 加受控檢索分支**

在問答 pipeline（`condense_question` 之後、`stream_answer` 之前）加：

```python
# backend/qa.py（示意，接你現有 async 問答流程）
from backend.dept_query import extract_slots, build_dept_filter
from backend.retrieval_openai import course_ids_from_search_results

async def retrieve_filtered_context(condensed_query: str, vs_id: str):
    """有系所/學制條件時：後端受控 search + 硬篩。回 (context_text, course_ids) 或 None（無條件）。"""
    slots = await extract_slots(condensed_query)
    filt = build_dept_filter(slots)
    if not filt:
        return None
    search_kwargs = dict(vector_store_id=vs_id, query=condensed_query, max_num_results=QA_MAX_RESULTS)
    resp = await _client.vector_stores.search(**search_kwargs, filters=filt)
    data = list(resp.data)
    if not data and filt.get("type") == "and":       # 放寬：只留 dept
        dept_only = next((c for c in filt["filters"] if c["key"] == "dept_canonical"), None)
        if dept_only:
            resp = await _client.vector_stores.search(**search_kwargs, filters=dept_only)
            data = list(resp.data)
    if not data:                                       # 仍 0 → 放棄過濾，回 None 走純語意
        return None
    ctx = "\n\n".join(
        f"課名：{d.filename}｜系所：{d.attributes.get('dept_canonical')}｜學制：{d.attributes.get('degree_level')}\n"
        + "".join(c.text for c in d.content)[:1500]
        for d in data
    )
    return ctx, course_ids_from_search_results(data)
```

生成端：當 `retrieve_filtered_context` 回非 None，用「注入 context、**不掛 file_search tool**」的 responses 串流生成（system prompt 用現有 `_SYSTEM_INSTRUCTION` + 追加「只依下列已檢索課程作答，勿臆造」）；citations 用回傳的 course_ids。回 None 時**完全走現狀**（既有 file_search 路徑，一字不改）。

> 關鍵：不要動「無條件」的既有路徑（回歸風險）。分支只在 filter 存在時生效。`QA_MAX_RESULTS` 沿用既有 `max_num_results`（目前 5），系所查詢建議提高到 10-12（在 attribute 硬篩下噪音已低）。

- [ ] **Step 5: 跑測試確認 GREEN + 全回歸**

Run: `cd /Users/albertpeng/Desktop/claude_project/NCCU-POC && ALLOWED_ORIGIN=* python -m pytest tests/backend -q`
Expected: 新測試 PASS，且既有測試數字**不少於**基準（無回歸）。

- [ ] **Step 6: Commit**

```bash
git add backend/qa.py tests/backend/test_qa_dept_filter.py
git commit -m "feat(dept): qa.py 系所感知受控檢索分支（硬篩+放寬 fallback）"
```

---

## Task 8: 真後端 + 真瀏覽器 e2e 驗證

**Files:**
- Create: `tests/e2e/test_dept_filter_e2e.py`（或沿用專案既有 Playwright e2e 形式）

- [ ] **Step 1: 起真後端**

```bash
cd /Users/albertpeng/Desktop/claude_project/NCCU-POC
OPENAI_API_KEY=$OPENAI_API_KEY OPENAI_VECTOR_STORE_ID=$OPENAI_VECTOR_STORE_ID \
  ALLOWED_ORIGIN=* DATABASE_URL=$DATABASE_PUBLIC_URL python -m uvicorn backend.main:app --port 8000
```

- [ ] **Step 2: e2e 情境（Playwright 真瀏覽器；清 stale route mock 見 memory `playwright-mcp-stale-route-mock`）**

逐一走使用者實際操作，讀回應驗證：
1. 問「推薦給我 10 門歷史系的課」→ 回應表格「系所」欄**全部是歷史學系**（0 外系）。
2. 問「歷史系碩士的課」→ 全部碩士/碩博、且系所全歷史學系。
3. 問「我只要大學部輕鬆的課」→ 全部學士（不限系）。
4. 問「有什麼有趣的課」（模糊）→ 正常回答、**行為同修改前**、非 0 筆。
5. 問「歷史學院的課」/「文苑的課」（別名/錯字）→ 正規化成功、回文學院相關；對不上則退純語意不崩。

- [ ] **Step 3: 5x consecutive 穩定性**

情境 1 連跑 5 次，每次都「系所全歷史學系」才算綠（0 flake）。記錄每次 pass/total。

- [ ] **Step 4: 截圖存證**

情境 1 回應截 PNG，Director cold-Read 確認「系所欄全歷史」後才進 review。

---

## Task 9: Code review + 獨立稽核

- [ ] **Step 1: code review skill 跑 diff**

對 `feat/department-filter` 分支 diff 跑五面向 review（correctness/readability/architecture/security/performance）。修 must-fix。

- [ ] **Step 2: 全然獨立的 auditor agent（不繼承實作脈絡）**

另派一個 fresh-context agent，給它 spec + 分支 diff，要求對抗性稽核：
- (a) 隨機抽 20 筆 `dept_mapping.json` 對照 course 原始 department + course_id，判對錯；特別驗碩博/通識/6 歧義詞幹。
- (b) **獨立**重跑 e2e「歷史系的課」與「大學部的課」，親證系所/學制正確（不看實作者結論）。
- (c) 查回歸：未指定系所的既有路徑有無被動到；`test_no_gemini`、`test_recommend_loop_fix`、`test_frontend_assets_shipped` 是否仍綠。
- 產出稽核報告：通過/退件 + 具體證據。退件項回到對應 Task 修。

- [ ] **Step 3: finishing-a-development-branch**

稽核通過後，用 `superpowers:finishing-a-development-branch` 決定 merge/PR，並依 live-demo-gate 截圖給使用者親眼確認後才進 master/部署。

---

## Self-Review（對照 spec）

- 目標「指定系所只回該系」→ Task 4（attribute）+ Task 7（filter 檢索）+ Task 8 情境 1 ✓
- 「學士/碩士分開」「只要大學部」→ Task 1（infer_degree_level）+ Task 6（degree filter/in 展開）+ Task 8 情境 2/3 ✓
- 「未指定行為不變」→ Task 7 Step 4「回 None 走現狀」+ Task 8 情境 4 ✓
- 「不誤過濾成 0 筆」→ Task 7 放寬 fallback + Task 5 對不上回 None ✓
- 變體/錯字（文院/文苑）→ Task 5 三層 + Task 8 情境 5 ✓
- college facet → Task 1/4/6 ✓
- 官方 canonical 來源 → Task 1（引 vocab doc）✓
- 三層正規化（regex 不用於查詢端）→ Task 5（alias+rapidfuzz，embedding 為擴充點）✓
- 執行方法論（TDD/並行/review/獨立稽核）→ 各 Task TDD + 檔案結構並行分工 + Task 9 ✓
- 非目標（不改 recommend、不重建 store、不做年級細粒度）→ 計畫未涉及 ✓
```
