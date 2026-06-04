"""高併發 async backfill：用 upload_to_file_search_store（一步上傳+索引）+ asyncio 並行。

相對 backfill_store.py(兩步 files.upload+import_file、ThreadPool x3 ~12/min)，
本版用 async client + semaphore 高併發 + 一步 API，目標大幅提升吞吐。

環境變數：
  GEMINI_API_KEY（必要）、BACKFILL_STORE（既有 store；空則新建）、
  CONCURRENCY（並行數，預設 16）、LIMIT（只灌前 N 筆，測試用；0=全部）、
  ONLY_FAILED=1（只灌 failed_courses.json 的 upload_failed）
輸出：印 FILE_SEARCH_STORE_NAME + active_documents_count + 吞吐率
"""
import os
import sys
import json
import time
import asyncio
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
CACHE = Path("ingestion/docs_cache.jsonl")
CONCURRENCY = int(os.environ.get("CONCURRENCY", "16"))
LIMIT = int(os.environ.get("LIMIT", "0"))
ONLY_FAILED = os.environ.get("ONLY_FAILED") == "1"
POLL_DEADLINE = 240


def load_docs():
    docs = [json.loads(l) for l in CACHE.read_text(encoding="utf-8").splitlines() if l.strip()]
    if ONLY_FAILED:
        # 優先讀本 async run 自己的失敗清單；無則退回 run.py 的 upload_failed
        bf = Path("ingestion/backfill_failed.json")
        if bf.exists():
            failed = set(json.loads(bf.read_text()))
        else:
            failed = set(json.loads(Path("ingestion/failed_courses.json").read_text())["upload_failed"])
        docs = [d for d in docs if d["course_id"] in failed]
    if LIMIT > 0:
        docs = docs[:LIMIT]
    return docs


async def upload_one(client, store, d, sem, counter):
    async with sem:
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
                f.write(d["doc_text"])
                tmp = Path(f.name)
            cfg = types.UploadToFileSearchStoreConfig(
                custom_metadata=[
                    {"key": "course_id", "string_value": d["course_id"]},
                    {"key": "syllabus_url", "string_value": d.get("syllabus_url", "")},
                ],
            )
            op = await client.aio.file_search_stores.upload_to_file_search_store(
                file_search_store_name=store, file=str(tmp), config=cfg,
            )
            deadline = time.time() + POLL_DEADLINE
            while not op.done:
                if time.time() > deadline:
                    raise TimeoutError("poll timeout")
                await asyncio.sleep(2)
                op = await client.aio.operations.get(op)
            if op.error:
                raise RuntimeError(str(op.error))
            counter["ok"] += 1
            ok = True
        except Exception as e:
            counter["fail"] += 1
            counter["last_err"] = repr(e)[:140]
            ok = False
        finally:
            if tmp:
                tmp.unlink(missing_ok=True)
        counter["done"] += 1
        if counter["done"] % 50 == 0:
            el = time.time() - counter["t0"]
            print(f"  {counter['done']}/{counter['total']} done "
                  f"({counter['fail']} fail) {counter['done']/el*60:.0f}/min", flush=True)
        return ok, d


async def main():
    client = genai.Client(
        api_key=os.environ["GEMINI_API_KEY"],
        http_options=types.HttpOptions(timeout=240_000),
    )
    docs = load_docs()
    print(f"[async] {len(docs)} docs, concurrency={CONCURRENCY}, only_failed={ONLY_FAILED}, limit={LIMIT}")

    store = os.environ.get("BACKFILL_STORE", "").split(" #")[0].strip()
    if not store:
        s = await client.aio.file_search_stores.create(config={"display_name": "nccu-courses-1142"})
        store = s.name
    print(f"[async] store = {store}")

    sem = asyncio.Semaphore(CONCURRENCY)
    counter = {"ok": 0, "fail": 0, "done": 0, "total": len(docs), "t0": time.time(), "last_err": ""}
    results = await asyncio.gather(*[upload_one(client, store, d, sem, counter) for d in docs])
    failed = [d["course_id"] for ok, d in results if not ok]

    el = time.time() - counter["t0"]
    info = await client.aio.file_search_stores.get(name=store)
    print(f"\n{'='*60}")
    print(f"FILE_SEARCH_STORE_NAME={store}")
    print(f"active_documents_count={info.active_documents_count}  failed={len(failed)}  "
          f"elapsed={el:.0f}s  rate={counter['done']/el*60:.0f}/min")
    if counter["last_err"]:
        print(f"last_err sample: {counter['last_err']}")
    print('='*60)
    if failed:
        Path("ingestion/backfill_failed.json").write_text(json.dumps(failed, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
