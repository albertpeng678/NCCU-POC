# Ingestion Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Parallel failure handling:** If multiple tasks fail independently during testing, use `superpowers:dispatching-parallel-agents` — one agent per broken domain.

**Goal:** Build a one-time Python script that downloads 2877 NCCU course syllabi, generates skill bridge tags via Gemini, and uploads all documents to a Gemini File Search Store.

**Architecture:** Download `CoursesList.xlsx` → parse 2877 courses → async scrape each syllabus with retry → batch Gemini skill-tag generation → upload to File Search Store → output `courses_meta.json` + `FILE_SEARCH_STORE_NAME`.

**Tech Stack:** Python 3.11+, httpx[asyncio], BeautifulSoup4/lxml, openpyxl, google-genai>=1.0, python-dotenv, pytest, pytest-asyncio

---

## File Map

| File | Responsibility |
|------|---------------|
| `ingestion/run.py` | Entry point — orchestrates all steps |
| `ingestion/xlsx_parser.py` | Download XLSX → parse 2877 rows → produce `courses_meta.json` |
| `ingestion/scraper.py` | Async HTTP fetch of syllabus HTML pages with retry |
| `ingestion/skill_tagger.py` | Batch Gemini calls to generate skill bridge paragraphs |
| `ingestion/uploader.py` | Upload documents to Gemini File Search Store |
| `ingestion/requirements.txt` | Python dependencies |
| `tests/ingestion/test_xlsx_parser.py` | Unit tests for XLSX parsing |
| `tests/ingestion/test_scraper.py` | Unit tests for scraper utilities |
| `tests/ingestion/test_skill_tagger.py` | Unit tests for skill tag formatting |

---

## Task 1: Project Setup

**Files:**
- Create: `ingestion/requirements.txt`
- Create: `ingestion/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/ingestion/__init__.py`
- Modify: `.gitignore` (already exists — verify `courses_meta.json` is listed)

- [ ] **Step 1: Create requirements.txt**

```
httpx[http2]>=0.27
beautifulsoup4>=4.12
lxml>=5.3
openpyxl>=3.1
google-genai>=1.0
python-dotenv>=1.0
pytest>=8.0
pytest-asyncio>=0.24
```

- [ ] **Step 2: Create empty `__init__.py` files**

```bash
touch ingestion/__init__.py tests/__init__.py tests/ingestion/__init__.py
```

- [ ] **Step 3: Install dependencies**

```bash
cd C:/side/NCCU-poc
pip install -r ingestion/requirements.txt
```

Expected: All packages install without error.

- [ ] **Step 4: Verify .gitignore covers sensitive outputs**

Run: `cat .gitignore | grep courses_meta`
Expected output: `courses_meta.json`

- [ ] **Step 5: Commit**

```bash
git add ingestion/requirements.txt ingestion/__init__.py tests/__init__.py tests/ingestion/__init__.py
git commit -m "chore: ingestion pipeline scaffold and dependencies"
```

---

## Task 2: XLSX Parser → `courses_meta.json`

**Files:**
- Create: `ingestion/xlsx_parser.py`
- Create: `tests/ingestion/test_xlsx_parser.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/ingestion/test_xlsx_parser.py
import pytest
from ingestion.xlsx_parser import course_id_to_url, parse_xlsx_row, build_courses_meta

def test_course_id_to_url_standard():
    url = course_id_to_url("000211012")
    assert url == "https://newdoc.nccu.edu.tw/teaschm/1142/schmPrv.jsp-yy=114&smt=2&num=000211&gop=01&s=2.html"

def test_course_id_to_url_zero_gop():
    url = course_id_to_url("000216001")
    assert url == "https://newdoc.nccu.edu.tw/teaschm/1142/schmPrv.jsp-yy=114&smt=2&num=000216&gop=00&s=1.html"

def test_parse_xlsx_row_standard():
    row = ("000211012", 3, "政治學                    ", "Political science", "蔡中民  ", "TSAI", "政治系  ", "Dept", "一D56", "mon13-16", "Room", "必/Required", "中文/Mandarin", "否/No", "note", "remark")
    result = parse_xlsx_row(row)
    assert result["course_id"] == "000211012"
    assert result["name"] == "政治學"
    assert result["credits"] == 3.0
    assert result["department"] == "政治系"
    assert result["teacher"] == "蔡中民"
    assert result["kind"] == "必修"
    assert result["source"] == "pending"
    assert "syllabus_url" in result

def test_parse_xlsx_row_skips_non_9digit_id():
    row = ("HEADER", None, "title", None, None, None, None, None, None, None, None, None, None, None, None, None)
    assert parse_xlsx_row(row) is None

def test_build_courses_meta_deduplicates_same_course_id():
    rows = [
        {"course_id": "000211012", "name": "政治學", "credits": 3.0, "department": "政治系",
         "teacher": "蔡中民", "kind": "必修", "syllabus_url": "https://x.com/a", "source": "pending"},
        {"course_id": "000211012", "name": "政治學", "credits": 3.0, "department": "政治系",
         "teacher": "蔡中民", "kind": "必修", "syllabus_url": "https://x.com/a", "source": "pending"},
    ]
    meta = build_courses_meta(rows)
    assert len(meta) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/ingestion/test_xlsx_parser.py -v
```

Expected: `ImportError` or `ModuleNotFoundError` — functions not yet defined.

- [ ] **Step 3: Implement `ingestion/xlsx_parser.py`**

```python
# ingestion/xlsx_parser.py
from __future__ import annotations
import re
import ssl
import urllib.request
import openpyxl
from pathlib import Path

XLSX_URL = "https://newdoc.nccu.edu.tw/teaschm/CoursesList.xlsx"
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE
_SSL_CTX.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)


def course_id_to_url(course_id: str) -> str:
    """Convert 9-digit course ID to syllabus URL."""
    num = course_id[:6]
    gop = course_id[6:8]
    s = course_id[8]
    return (
        f"https://newdoc.nccu.edu.tw/teaschm/1142/schmPrv.jsp"
        f"-yy=114&smt=2&num={num}&gop={gop}&s={s}.html"
    )


def parse_xlsx_row(row: tuple) -> dict | None:
    """Parse one XLSX data row. Returns None if row is not a valid course."""
    course_id = str(row[0]).strip() if row[0] else ""
    if not re.fullmatch(r"\d{9}", course_id):
        return None

    name_raw = str(row[2]).strip() if row[2] else ""
    name = name_raw.split("\n")[0].strip()

    teacher_raw = str(row[4]).strip() if row[4] else ""
    teacher = teacher_raw.split("\n")[0].strip()

    dept_raw = str(row[6]).strip() if row[6] else ""
    department = dept_raw.split("\n")[0].strip()

    kind_raw = str(row[11]).strip() if row[11] else ""
    kind = "必修" if "必" in kind_raw else "選修"

    try:
        credits = float(row[1]) if row[1] is not None else 0.0
    except (ValueError, TypeError):
        credits = 0.0

    return {
        "course_id": course_id,
        "name": name,
        "credits": credits,
        "department": department,
        "teacher": teacher,
        "kind": kind,
        "syllabus_url": course_id_to_url(course_id),
        "source": "pending",
    }


def build_courses_meta(rows: list[dict]) -> dict[str, dict]:
    """Deduplicate by course_id and return keyed dict."""
    meta = {}
    for row in rows:
        if row["course_id"] not in meta:
            meta[row["course_id"]] = row
    return meta


def download_and_parse_xlsx(xlsx_path: Path | None = None) -> dict[str, dict]:
    """Download XLSX and parse all courses. Returns courses_meta dict."""
    if xlsx_path is None:
        req = urllib.request.Request(
            XLSX_URL,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://qrysub.nccu.edu.tw/"},
        )
        with urllib.request.urlopen(req, context=_SSL_CTX) as resp:
            data = resp.read()
        tmp_path = Path("/tmp/CoursesList.xlsx")
        tmp_path.write_bytes(data)
        xlsx_path = tmp_path

    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    ws = wb.active
    rows = []
    for row in ws.iter_rows(min_row=3, values_only=True):  # skip 2 header rows
        parsed = parse_xlsx_row(row)
        if parsed:
            rows.append(parsed)
    wb.close()
    return build_courses_meta(rows)
```

- [ ] **Step 4: Run tests — verify pass**

```bash
pytest tests/ingestion/test_xlsx_parser.py -v
```

Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add ingestion/xlsx_parser.py tests/ingestion/test_xlsx_parser.py
git commit -m "feat(ingestion): XLSX parser with course_id→URL formula"
```

---

## Task 3: Async Syllabus Scraper

**Files:**
- Create: `ingestion/scraper.py`
- Create: `tests/ingestion/test_scraper.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/ingestion/test_scraper.py
import pytest
from ingestion.scraper import extract_text_from_html, build_document_text

def test_extract_text_from_html_returns_meaningful_content():
    html = """<html><body>
    <h2>政治學</h2>
    <div>課程簡介</div>
    <p>旨在致力基礎學科之訓練，培養對政治學的興趣。</p>
    <script>var x = 1;</script>
    <style>.nav{color:red}</style>
    </body></html>"""
    text = extract_text_from_html(html)
    assert "政治學" in text
    assert "旨在致力基礎學科" in text
    assert "var x = 1" not in text  # scripts stripped
    assert ".nav" not in text         # styles stripped

def test_extract_text_from_html_empty_returns_empty_string():
    assert extract_text_from_html("") == ""
    assert extract_text_from_html("<html></html>") == ""

def test_build_document_text_includes_course_id_header():
    meta = {"course_id": "000211012", "name": "政治學", "department": "政治系",
            "teacher": "蔡中民", "credits": 3.0, "kind": "必修",
            "syllabus_url": "https://x.com/a", "source": "pending"}
    syllabus_text = "課程介紹內容..."
    skill_bridge = "培養技能：政治分析\n適合職涯：公務員"
    doc = build_document_text(meta, syllabus_text, skill_bridge)
    assert "課程代號: 000211012" in doc
    assert "政治學" in doc
    assert "課程介紹內容" in doc
    assert "培養技能：政治分析" in doc

def test_build_document_text_with_no_syllabus_uses_name_only():
    meta = {"course_id": "000211012", "name": "政治學", "department": "政治系",
            "teacher": "蔡中民", "credits": 3.0, "kind": "必修",
            "syllabus_url": "https://x.com/a", "source": "name_only"}
    doc = build_document_text(meta, syllabus_text=None, skill_bridge="培養技能：政治分析")
    assert "課程代號: 000211012" in doc
    assert "政治學" in doc
    assert "培養技能：政治分析" in doc
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/ingestion/test_scraper.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement `ingestion/scraper.py`**

```python
# ingestion/scraper.py
from __future__ import annotations
import asyncio
import ssl
import httpx
from bs4 import BeautifulSoup

_SEMAPHORE = asyncio.Semaphore(20)
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE
_SSL_CTX.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)


def extract_text_from_html(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines) if lines else ""


def build_document_text(meta: dict, syllabus_text: str | None, skill_bridge: str) -> str:
    header = (
        f"課程代號: {meta['course_id']}\n"
        f"課程名稱: {meta['name']}\n"
        f"開課系所: {meta['department']}\n"
        f"授課教師: {meta['teacher']}\n"
        f"學分: {meta['credits']}\n"
        f"修別: {meta['kind']}\n\n"
    )
    body = syllabus_text or f"[無完整課綱，以課程名稱推估] 課程名稱：{meta['name']}"
    bridge = f"\n\n=== 課程技能對應（自動生成）===\n課程代號: {meta['course_id']}\n{skill_bridge}"
    return header + body + bridge


async def fetch_syllabus_text(client: httpx.AsyncClient, url: str) -> str | None:
    """Fetch syllabus HTML and extract text. Returns None after 3 failed attempts."""
    for attempt in range(3):
        try:
            async with _SEMAPHORE:
                resp = await client.get(url, timeout=15.0)
                resp.raise_for_status()
                return extract_text_from_html(resp.text)
        except Exception:
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
    return None


def make_httpx_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        verify=False,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://qrysub.nccu.edu.tw/"},
        follow_redirects=True,
    )
```

- [ ] **Step 4: Run tests — verify pass**

```bash
pytest tests/ingestion/test_scraper.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add ingestion/scraper.py tests/ingestion/test_scraper.py
git commit -m "feat(ingestion): async syllabus scraper with HTML text extraction"
```

---

## Task 4: Skill Tagger (Batched Gemini)

**Files:**
- Create: `ingestion/skill_tagger.py`
- Create: `tests/ingestion/test_skill_tagger.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/ingestion/test_skill_tagger.py
from ingestion.skill_tagger import format_skill_bridge, chunk_list

def test_format_skill_bridge_contains_required_sections():
    raw = "培養技能：產品管理、用戶研究\n適合職涯：PM\n關鍵詞：roadmap"
    bridge = format_skill_bridge(raw)
    assert "培養技能" in bridge
    assert "適合職涯" in bridge

def test_format_skill_bridge_with_empty_input_returns_fallback():
    bridge = format_skill_bridge("")
    assert isinstance(bridge, str)
    assert len(bridge) > 0

def test_chunk_list_splits_correctly():
    items = list(range(25))
    chunks = chunk_list(items, 10)
    assert len(chunks) == 3
    assert chunks[0] == list(range(10))
    assert chunks[1] == list(range(10, 20))
    assert chunks[2] == list(range(20, 25))

def test_chunk_list_exact_multiple():
    items = list(range(20))
    chunks = chunk_list(items, 10)
    assert len(chunks) == 2

def test_chunk_list_single_item():
    chunks = chunk_list([1], 10)
    assert chunks == [[1]]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/ingestion/test_skill_tagger.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement `ingestion/skill_tagger.py`**

```python
# ingestion/skill_tagger.py
from __future__ import annotations
import json
import time
from google import genai

BATCH_SIZE = 10  # courses per Gemini call
RATE_LIMIT_DELAY = 1.1  # seconds between calls (free tier: 60 QPM)


def chunk_list(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def format_skill_bridge(raw: str) -> str:
    if not raw or not raw.strip():
        return "培養技能：通識素養\n適合職涯：多元職涯\n關鍵詞：學術研究"
    return raw.strip()


def generate_skill_bridges(
    client: genai.Client,
    courses: list[dict],
) -> list[str]:
    """Batch-generate skill bridge paragraphs for a list of courses.
    
    Returns list of skill bridge strings, same order as input.
    """
    all_bridges: list[str] = []

    for batch in chunk_list(courses, BATCH_SIZE):
        names = "\n".join(
            f"{i + 1}. 課程名稱：{c['name']} ｜ 系所：{c['department']}"
            for i, c in enumerate(batch)
        )
        prompt = f"""以下是 {len(batch)} 門大學課程。請為每門課生成技能對應段落。

格式（每門課一個段落，純字串）：
培養技能：技能1、技能2、技能3（3-6項）
適合職涯：職涯1、職涯2（2-4項）
關鍵詞：keyword1、keyword2、keyword3（3-6項，中英混合）

課程列表：
{names}

回傳 JSON 陣列，共 {len(batch)} 個字串元素，順序對應課程列表。"""

        try:
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            bridges = json.loads(resp.text)
            if not isinstance(bridges, list) or len(bridges) != len(batch):
                raise ValueError(f"Expected {len(batch)} items, got {len(bridges)}")
            all_bridges.extend(format_skill_bridge(b) for b in bridges)
        except Exception:
            # Fallback: empty bridge for each course in batch
            all_bridges.extend(
                f"培養技能：通識素養\n適合職涯：多元職涯\n關鍵詞：{c['name']}"
                for c in batch
            )

        time.sleep(RATE_LIMIT_DELAY)

    return all_bridges
```

- [ ] **Step 4: Run tests — verify pass**

```bash
pytest tests/ingestion/test_skill_tagger.py -v
```

Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add ingestion/skill_tagger.py tests/ingestion/test_skill_tagger.py
git commit -m "feat(ingestion): batched Gemini skill bridge tag generator"
```

---

## Task 5: File Search Store Uploader

**Files:**
- Create: `ingestion/uploader.py`

> No unit tests for uploader — Gemini SDK interactions are integration-level. Uploader is tested via Task 6 dry-run.

- [ ] **Step 1: Create `ingestion/uploader.py`**

```python
# ingestion/uploader.py
from __future__ import annotations
import tempfile
import time
from pathlib import Path
from google import genai


def create_store(client: genai.Client, display_name: str = "nccu-courses-1142") -> str:
    """Create a new File Search Store. Returns store name."""
    store = client.file_search_stores.create(display_name=display_name)
    print(f"[uploader] Created store: {store.name}")
    return store.name


def upload_document(
    client: genai.Client,
    store_name: str,
    course_id: str,
    syllabus_url: str,
    document_text: str,
) -> bool:
    """Upload one course document to the File Search Store.
    
    Returns True on success, False on failure.
    """
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write(document_text)
            tmp_path = Path(f.name)

        # Upload file to Gemini Files API
        uploaded = client.files.upload(
            file=tmp_path,
            config={"display_name": f"course-{course_id}"},
        )
        tmp_path.unlink(missing_ok=True)

        # Import into File Search Store
        op = client.file_search_stores.import_file(
            file_search_store_name=store_name,
            file_name=uploaded.name,
            config={
                "custom_metadata": [
                    {"key": "course_id", "string_value": course_id},
                    {"key": "syllabus_url", "string_value": syllabus_url},
                ]
            },
        )
        op.result(timeout=60)  # Wait for indexing
        return True

    except Exception as e:
        print(f"[uploader] Failed to upload {course_id}: {e}")
        return False
```

- [ ] **Step 2: Commit**

```bash
git add ingestion/uploader.py
git commit -m "feat(ingestion): Gemini File Search Store uploader"
```

---

## Task 6: Orchestration — `ingestion/run.py`

**Files:**
- Create: `ingestion/run.py`

- [ ] **Step 1: Create `ingestion/run.py`**

```python
# ingestion/run.py
"""One-time ingestion pipeline. Run from project root:
  python -m ingestion.run
Outputs: backend/courses_meta.json, console prints FILE_SEARCH_STORE_NAME.
"""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
from dotenv import load_dotenv
from google import genai

from ingestion.xlsx_parser import download_and_parse_xlsx
from ingestion.scraper import fetch_syllabus_text, build_document_text, make_httpx_client
from ingestion.skill_tagger import generate_skill_bridges, chunk_list
from ingestion.uploader import create_store, upload_document

load_dotenv()
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
OUTPUT_META = Path("backend/courses_meta.json")
FAILED_LOG = Path("ingestion/failed_courses.json")


async def scrape_all(courses: list[dict]) -> dict[str, str | None]:
    """Scrape all syllabus pages concurrently. Returns {course_id: text_or_None}."""
    async with make_httpx_client() as client:
        tasks = {
            c["course_id"]: fetch_syllabus_text(client, c["syllabus_url"])
            for c in courses
        }
        results = {}
        for cid, coro in tasks.items():
            results[cid] = await coro
        return results


def main():
    client = genai.Client(api_key=GEMINI_API_KEY)

    # Step 1: Download and parse XLSX
    print("[run] Downloading CoursesList.xlsx...")
    meta = download_and_parse_xlsx()
    courses = list(meta.values())
    print(f"[run] {len(courses)} courses parsed.")

    # Step 2: Scrape syllabi
    print("[run] Scraping syllabi (async)...")
    syllabus_texts = asyncio.run(scrape_all(courses))
    
    failed = [cid for cid, text in syllabus_texts.items() if text is None]
    print(f"[run] Scraped: {len(courses) - len(failed)} ok, {len(failed)} failed.")
    for cid in failed:
        meta[cid]["source"] = "name_only"

    # Step 3: Generate skill bridges (batched)
    print("[run] Generating skill bridges via Gemini...")
    bridges = generate_skill_bridges(client, courses)
    bridge_map = {c["course_id"]: b for c, b in zip(courses, bridges)}

    # Step 4: Build document texts
    docs = {}
    for cid, course_meta in meta.items():
        docs[cid] = build_document_text(
            meta=course_meta,
            syllabus_text=syllabus_texts.get(cid),
            skill_bridge=bridge_map[cid],
        )
        course_meta["source"] = "name_only" if syllabus_texts.get(cid) is None else "syllabus"

    # Step 5: Upload to File Search Store
    print("[run] Creating File Search Store...")
    store_name = create_store(client)

    print(f"[run] Uploading {len(docs)} documents...")
    upload_failures = []
    for i, (cid, doc_text) in enumerate(docs.items(), 1):
        ok = upload_document(client, store_name, cid, meta[cid]["syllabus_url"], doc_text)
        if not ok:
            upload_failures.append(cid)
        if i % 100 == 0:
            print(f"[run]   {i}/{len(docs)} uploaded...")

    # Step 6: Save outputs
    OUTPUT_META.parent.mkdir(exist_ok=True)
    OUTPUT_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[run] Saved {OUTPUT_META}")

    if failed or upload_failures:
        FAILED_LOG.write_text(
            json.dumps({"scrape_failed": failed, "upload_failed": upload_failures}, ensure_ascii=False, indent=2)
        )
        print(f"[run] Failed courses logged to {FAILED_LOG}")

    print(f"\n{'='*60}")
    print(f"FILE_SEARCH_STORE_NAME={store_name}")
    print(f"{'='*60}")
    print("Copy the above value into your Railway backend environment variables.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Dry-run with first 5 courses only (validate pipeline)**

Temporarily modify `main()` to `courses = list(meta.values())[:5]`, then:

```bash
python -m ingestion.run
```

Expected output:
```
[run] Downloading CoursesList.xlsx...
[run] 2877 courses parsed.
[run] Scraping syllabi (async)...
[run] Scraped: X ok, Y failed.
[run] Generating skill bridges via Gemini...
[run] Creating File Search Store...
[run] Created store: fileSearchStores/...
[run] Uploading 5 documents...
...
FILE_SEARCH_STORE_NAME=fileSearchStores/abc123...
```

- [ ] **Step 3: Revert test limit and run full pipeline**

Revert the `[:5]` slice. Run:
```bash
python -m ingestion.run
```

Expected: Completes without crash. `backend/courses_meta.json` contains ~2877 entries.

- [ ] **Step 4: Verify output**

```bash
python -c "import json; d=json.load(open('backend/courses_meta.json')); print(len(d), 'courses'); print(list(d.keys())[:3])"
```

Expected: `2877 courses` (or close). At least 3 course IDs printed.

- [ ] **Step 5: Commit**

```bash
git add ingestion/run.py
git commit -m "feat(ingestion): orchestration pipeline run.py"
```

---

## Task 7: Run Full Suite + Final Verification

- [ ] **Step 1: Run all ingestion tests**

```bash
pytest tests/ingestion/ -v
```

Expected: All tests PASS.

- [ ] **Step 2: Verify `superpowers:verification-before-completion`**

```bash
python -c "
import json
d = json.load(open('backend/courses_meta.json'))
assert len(d) >= 2000, f'Too few courses: {len(d)}'
sample = list(d.values())[0]
assert 'syllabus_url' in sample
assert 'newdoc.nccu.edu.tw' in sample['syllabus_url']
assert sample['source'] in ('syllabus', 'name_only')
print('Verification PASSED:', len(d), 'courses')
"
```

Expected: `Verification PASSED: XXXX courses`

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat(ingestion): complete pipeline — scrape, skill-tag, upload"
```
