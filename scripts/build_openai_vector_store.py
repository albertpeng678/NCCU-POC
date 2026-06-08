"""
離線建庫腳本：從 ingestion/docs_cache.jsonl 把 2718 課上傳到 OpenAI Vector Store。

使用方式：
    OPENAI_API_KEY=sk-... python scripts/build_openai_vector_store.py

冪等模式（跳過已存在的課）：
    ONLY_MISSING=1 OPENAI_API_KEY=sk-... python scripts/build_openai_vector_store.py

完成後印出：
    OPENAI_VECTOR_STORE_ID=vs_xxxxxxx

注意：main() 是純 I/O，不寫單元測試。只測 filename_for / file_attributes / load_docs。
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any


# ──────────────────────────────
# 純函式（可單元測試）
# ──────────────────────────────

def filename_for(course_id: str) -> str:
    """回傳上傳到 OpenAI Files 時使用的檔名：{course_id}.txt。"""
    return f"{course_id}.txt"


def file_attributes(rec: dict) -> dict:
    """從文件記錄萃取要掛到 vector store file 的 attributes。"""
    return {
        "course_id": rec["course_id"],
        "syllabus_url": rec.get("syllabus_url", ""),
    }


def load_docs(path: str) -> list[dict]:
    """讀取 JSONL 文件快取，回傳 list[dict]（每行一筆）。"""
    docs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                docs.append(json.loads(line))
    return docs


# ──────────────────────────────
# I/O 主流程（不測，控制者用真 key 跑）
# ──────────────────────────────

_DOCS_PATH = Path(__file__).parent.parent / "ingestion" / "docs_cache.jsonl"
_BATCH_LOG = 100  # 每 N 課印進度
_CONCURRENCY = 8  # asyncio.Semaphore 控併發（避開 300 req/min）


async def main() -> None:
    from openai import AsyncOpenAI

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("Error: OPENAI_API_KEY not set")

    only_missing = os.environ.get("ONLY_MISSING", "").strip() == "1"
    docs_path = os.environ.get("DOCS_PATH", str(_DOCS_PATH))

    client = AsyncOpenAI(api_key=api_key, max_retries=3, timeout=180.0)

    # 修 3：重用既有 store（若 env 已設）或建新 store
    existing_vs_id = os.environ.get("OPENAI_VECTOR_STORE_ID", "").strip()
    if existing_vs_id:
        vs_id = existing_vs_id
        print(f"[build] Reusing existing vector store: {vs_id}")
    else:
        if only_missing:
            print(
                "[build] WARN: ONLY_MISSING=1 but OPENAI_VECTOR_STORE_ID not set "
                "— will create a NEW empty store. Set OPENAI_VECTOR_STORE_ID to reuse.",
                flush=True,
            )
        vs = await client.vector_stores.create(name="nccu-courses-1142")
        vs_id = vs.id
        print(f"[build] Vector store created: {vs_id}")

    docs = load_docs(docs_path)
    print(f"[build] Loaded {len(docs)} docs from {docs_path}")

    # 冪等：列出既有檔名
    existing_ids: set[str] = set()
    if only_missing:
        print("[build] ONLY_MISSING=1: listing existing files …")
        page = await client.vector_stores.files.list(vs_id)
        async for vf in page:
            # vf.id is file_id; we need filename — use attributes if available
            attrs = getattr(vf, "attributes", None) or {}
            cid = attrs.get("course_id")
            if cid:
                existing_ids.add(cid)
        print(f"[build] Already in store: {len(existing_ids)} courses")

    sem = asyncio.Semaphore(_CONCURRENCY)
    failed_ids: list[str] = []

    # 修 2：upload_one 容錯，失敗記錄不中斷
    async def upload_one(rec: dict) -> None:
        cid = rec["course_id"]
        if cid in existing_ids:
            return
        doc_bytes = rec["doc_text"].encode("utf-8")
        try:
            async with sem:
                file_obj = await client.files.create(
                    file=(filename_for(cid), doc_bytes),
                    purpose="assistants",
                )
                await client.vector_stores.files.create(
                    vector_store_id=vs_id,
                    file_id=file_obj.id,
                    attributes=file_attributes(rec),
                )
        except Exception as exc:
            failed_ids.append(cid)
            print(f"[build] WARN: {cid} failed: {exc}", flush=True)

    tasks = [upload_one(rec) for rec in docs]
    done = 0
    for coro in asyncio.as_completed(tasks):
        await coro
        done += 1
        if done % _BATCH_LOG == 0:
            print(f"[build] {done}/{len(tasks)} uploaded …")

    print(f"[build] Done. {done} processed (skipped {len(existing_ids)}, failed={len(failed_ids)}).")
    print(f"OPENAI_VECTOR_STORE_ID={vs_id}")

    if failed_ids:
        failed_path = Path(__file__).parent.parent / "ingestion" / "openai_build_failed.json"
        with open(failed_path, "w", encoding="utf-8") as f:
            json.dump(failed_ids, f, ensure_ascii=False, indent=2)
        print(f"[build] Failed IDs written to {failed_path}")


if __name__ == "__main__":
    asyncio.run(main())
