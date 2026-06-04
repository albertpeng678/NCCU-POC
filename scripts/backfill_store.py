"""從 docs_cache.jsonl 把課程文件灌進 File Search Store（不重爬/不重標技能）。

用途：full ingestion 的 scrape+skill_bridge 已完成並快取於 ingestion/docs_cache.jsonl，
但上傳因 Gemini 月度支出上限(429)中斷。等 cap 解除後跑這支，只做「上傳(embedding)」。

低併發 + 多輪重試 + 240s import timeout，避免 import_file 佇列雪崩。

用法：
  # 建新 store 灌全部
  .venv/bin/python scripts/backfill_store.py
  # 灌進既有 store（續灌；搭配 --failed 只灌失敗清單）
  BACKFILL_STORE=fileSearchStores/xxx .venv/bin/python scripts/backfill_store.py --failed

環境變數：
  GEMINI_API_KEY（必要）、BACKFILL_STORE（既有 store；空則新建）、
  UPLOAD_WORKERS（預設 3）
輸出：印出 FILE_SEARCH_STORE_NAME；失敗清單寫 ingestion/backfill_failed.json
"""
import os
import sys
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
from google import genai
from google.genai import types

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ingestion.uploader import create_store, upload_document  # noqa: E402

load_dotenv()
CACHE = Path("ingestion/docs_cache.jsonl")
FAILED_OUT = Path("ingestion/backfill_failed.json")
WORKERS = int(os.environ.get("UPLOAD_WORKERS", "3"))
ROUNDS = 3
only_failed = "--failed" in sys.argv


def load_docs() -> list[dict]:
    docs = [json.loads(line) for line in CACHE.read_text(encoding="utf-8").splitlines() if line.strip()]
    if only_failed:
        failed = set(json.loads(Path("ingestion/failed_courses.json").read_text())["upload_failed"])
        docs = [d for d in docs if d["course_id"] in failed]
    return docs


def upload_round(client, store, items, workers):
    failed, done = [], 0

    def up(d):
        ok = upload_document(client, store, d["course_id"], d.get("syllabus_url", ""), d["doc_text"])
        return d, ok

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for d, ok in ex.map(up, items):
            done += 1
            if not ok:
                failed.append(d)
            if done % 100 == 0 or done == len(items):
                print(f"  {done}/{len(items)} this round ({len(failed)} failed)...", flush=True)
    return failed


def main():
    client = genai.Client(
        api_key=os.environ["GEMINI_API_KEY"],
        http_options=types.HttpOptions(timeout=240_000),
    )
    docs = load_docs()
    print(f"[backfill] {len(docs)} docs to upload (only_failed={only_failed}, workers={WORKERS})")

    store = os.environ.get("BACKFILL_STORE", "").split(" #")[0].strip()
    if not store:
        store = create_store(client)
    print(f"[backfill] store = {store}")

    pending = docs
    for rnd in range(ROUNDS):
        workers = WORKERS if rnd == 0 else max(1, WORKERS // 2)
        print(f"[backfill] round {rnd + 1}/{ROUNDS}: {len(pending)} docs (workers={workers})")
        pending = upload_round(client, store, pending, workers)
        if not pending:
            break
        print(f"[backfill] round {rnd + 1} left {len(pending)} failed; retrying...", flush=True)

    s = client.file_search_stores.get(name=store)
    print(f"\n{'='*60}\nFILE_SEARCH_STORE_NAME={store}")
    print(f"active_documents_count={s.active_documents_count}  still_failed={len(pending)}\n{'='*60}")
    if pending:
        FAILED_OUT.write_text(json.dumps([d["course_id"] for d in pending], ensure_ascii=False, indent=2))
        print(f"[backfill] failed ids → {FAILED_OUT}")


if __name__ == "__main__":
    main()
