# ingestion/run.py
"""One-time ingestion pipeline. Run from project root:
  python -m ingestion.run
Outputs: backend/courses_meta.json, console prints FILE_SEARCH_STORE_NAME.
"""
from __future__ import annotations
import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from dotenv import load_dotenv
from google import genai
from google.genai import types

from ingestion.xlsx_parser import download_and_parse_xlsx
from ingestion.scraper import fetch_syllabus_text, build_document_text, make_httpx_client
from ingestion.skill_tagger import generate_skill_bridges, chunk_list, MAX_WORKERS
from ingestion.uploader import create_store, upload_document

load_dotenv()
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
OUTPUT_META = Path("backend/courses_meta.json")
FAILED_LOG = Path("ingestion/failed_courses.json")

# Set to a positive int to limit course count (for dry-run); None = all courses
DRY_RUN_LIMIT = int(os.environ.get("INGESTION_LIMIT", "0")) or None


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
    # 加上請求 timeout（毫秒）：避免單次 Gemini 呼叫 hung 住整個 ingestion。
    # 卡住的呼叫會在逾時後拋錯，由 skill_tagger/uploader 既有 try/except 接住並 fallback。
    client = genai.Client(
        api_key=GEMINI_API_KEY,
        http_options=types.HttpOptions(timeout=120_000),
    )

    # Step 1: Download and parse XLSX
    print("[run] Downloading CoursesList.xlsx...")
    meta = download_and_parse_xlsx()
    courses = list(meta.values())
    if DRY_RUN_LIMIT:
        courses = courses[:DRY_RUN_LIMIT]
        meta = {c["course_id"]: c for c in courses}
        print(f"[run] DRY RUN: limited to {DRY_RUN_LIMIT} courses")
    print(f"[run] {len(courses)} courses to process.")

    # Step 2: Scrape syllabi
    print("[run] Scraping syllabi (async)...")
    syllabus_texts = asyncio.run(scrape_all(courses))

    failed = [cid for cid, text in syllabus_texts.items() if text is None]
    print(f"[run] Scraped: {len(courses) - len(failed)} ok, {len(failed)} failed.")

    # Step 3: Generate skill bridges (batched)
    print("[run] Generating skill bridges via Gemini...")
    bridges = generate_skill_bridges(client, courses)
    bridge_map = {c["course_id"]: b for c, b in zip(courses, bridges)}

    # Step 4: Build document texts + set source
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

    print(f"[run] Uploading {len(docs)} documents (parallel x{MAX_WORKERS})...")
    upload_failures = []

    def _upload_one(item):
        cid, doc_text = item
        ok = upload_document(client, store_name, cid, meta[cid]["syllabus_url"], doc_text)
        return cid, ok

    done = 0
    total = len(docs)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for cid, ok in ex.map(_upload_one, list(docs.items())):
            if not ok:
                upload_failures.append(cid)
            done += 1
            if done % 100 == 0 or done == total:
                print(f"[run]   {done}/{total} uploaded ({len(upload_failures)} failed)...", flush=True)

    # Step 6: Save outputs
    OUTPUT_META.parent.mkdir(exist_ok=True)
    OUTPUT_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[run] Saved {OUTPUT_META}")

    if failed or upload_failures:
        FAILED_LOG.write_text(
            json.dumps({"scrape_failed": failed, "upload_failed": upload_failures}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[run] Failed courses logged to {FAILED_LOG}")

    print(f"\n{'='*60}")
    print(f"FILE_SEARCH_STORE_NAME={store_name}")
    print(f"{'='*60}")
    print("Copy the above value into your Railway backend environment variables.")


if __name__ == "__main__":
    main()
