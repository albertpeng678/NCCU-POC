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
DOCS_CACHE = Path("ingestion/docs_cache.jsonl")  # 文件文字快取（可續跑/廉價重試）

# Set to a positive int to limit course count (for dry-run); None = all courses
DRY_RUN_LIMIT = int(os.environ.get("INGESTION_LIMIT", "0")) or None
# import_file 為伺服器端索引，不耐高併發；上傳併發遠低於 skill bridges
UPLOAD_WORKERS = int(os.environ.get("UPLOAD_WORKERS", "4"))
UPLOAD_ROUNDS = 3  # 1 主回合 + 2 重試回合（逐回合降併發）


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

    # Step 4: Build document texts + set source；同時存檔（可續跑/廉價重試）
    docs = {}
    for cid, course_meta in meta.items():
        docs[cid] = build_document_text(
            meta=course_meta,
            syllabus_text=syllabus_texts.get(cid),
            skill_bridge=bridge_map[cid],
        )
        course_meta["source"] = "name_only" if syllabus_texts.get(cid) is None else "syllabus"

    with DOCS_CACHE.open("w", encoding="utf-8") as f:
        for cid, doc_text in docs.items():
            f.write(json.dumps(
                {"course_id": cid, "doc_text": doc_text, "syllabus_url": meta[cid]["syllabus_url"]},
                ensure_ascii=False) + "\n")
    print(f"[run] Cached doc texts → {DOCS_CACHE}")

    # Step 5: Upload to File Search Store（低併發 + 多輪重試，避免 import 佇列雪崩）
    print("[run] Creating File Search Store...")
    store_name = create_store(client)
    total = len(docs)

    def _upload_round(items, workers):
        """並行上傳一批，回傳仍失敗的 (cid, doc_text) list。"""
        failed = []
        done = 0

        def up(item):
            cid, doc_text = item
            ok = upload_document(client, store_name, cid, meta[cid]["syllabus_url"], doc_text)
            return item, ok

        with ThreadPoolExecutor(max_workers=workers) as ex:
            for item, ok in ex.map(up, items):
                done += 1
                if not ok:
                    failed.append(item)
                if done % 100 == 0 or done == len(items):
                    print(f"[run]   {done}/{len(items)} this round "
                          f"({len(failed)} failed so far)...", flush=True)
        return failed

    pending = list(docs.items())
    for rnd in range(UPLOAD_ROUNDS):
        workers = UPLOAD_WORKERS if rnd == 0 else max(2, UPLOAD_WORKERS // 2)
        print(f"[run] Upload round {rnd + 1}/{UPLOAD_ROUNDS}: {len(pending)} docs (workers={workers})...")
        pending = _upload_round(pending, workers)
        if not pending:
            break
        print(f"[run] Round {rnd + 1} left {len(pending)} failed; retrying...", flush=True)
    upload_failures = [cid for cid, _ in pending]
    print(f"[run] Upload complete: {total - len(upload_failures)}/{total} ok, {len(upload_failures)} failed.")

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
