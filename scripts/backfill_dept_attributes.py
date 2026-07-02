"""對既有 vector store 檔案補 dept_canonical/college/degree_level attribute（不重傳檔案）。

用法：
    OPENAI_API_KEY=... OPENAI_VECTOR_STORE_ID=... python scripts/backfill_dept_attributes.py --dry-run
    OPENAI_API_KEY=... OPENAI_VECTOR_STORE_ID=... python scripts/backfill_dept_attributes.py

--dry-run 只讀（list files）、建 course_id -> file_id 對照、計算每檔要寫的 attributes 並印統計，
完全不呼叫任何寫入 API（不 update、不 create）。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.dept_vocab import infer_degree_level

ROOT = Path(__file__).resolve().parent.parent
META = json.loads((ROOT / "backend/courses_meta.json").read_text(encoding="utf-8"))
MAPPING = json.loads((ROOT / "backend/dept_mapping.json").read_text(encoding="utf-8"))


def build_attributes(course_id: str, rec: dict, mapping: dict) -> dict:
    """回傳要 POST 到 vector store file 的完整 attributes（含既有 course_id/syllabus_url + 新 facets）。"""
    m = mapping.get(rec.get("department", ""), {})
    dc = m.get("dept_canonical", "其他")
    return {
        "course_id": course_id,
        "syllabus_url": rec.get("syllabus_url", ""),
        "dept_canonical": dc,
        "college": m.get("college", ""),
        "degree_level": infer_degree_level(rec.get("department", ""), course_id),
    }


async def _build_file_by_cid(client, vs: str) -> dict[str, str]:
    """列出 vector store 內所有檔案，建 course_id -> file_id 對照（attributes.course_id 已存在）。"""
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
    return file_by_cid


async def main() -> None:
    from openai import AsyncOpenAI

    dry = "--dry-run" in sys.argv
    vs = os.environ["OPENAI_VECTOR_STORE_ID"]
    client = AsyncOpenAI()

    file_by_cid = await _build_file_by_cid(client, vs)
    matched = [cid for cid in META if cid in file_by_cid]
    unmatched = [cid for cid in META if cid not in file_by_cid]

    print(f"store 檔案 {len(file_by_cid)}；meta {len(META)}", file=sys.stderr)
    print(f"對得上 {len(matched)}；對不上 {len(unmatched)}", file=sys.stderr)
    if unmatched:
        preview = unmatched[:10]
        print(f"對不上範例（最多 10 筆）：{preview}", file=sys.stderr)

    if dry:
        print("attributes 範例（前 3 筆）：", file=sys.stderr)
        for cid in matched[:3]:
            print(f"  {cid}: {build_attributes(cid, META[cid], MAPPING)}", file=sys.stderr)
        print(f"DRY updated {len(matched)} files (not written)")
        return

    sem = asyncio.Semaphore(16)
    done = 0

    async def one(cid: str, rec: dict) -> None:
        nonlocal done
        fid = file_by_cid.get(cid)
        if not fid:
            return
        attrs = build_attributes(cid, rec, MAPPING)
        async with sem:
            await client.vector_stores.files.update(vector_store_id=vs, file_id=fid, attributes=attrs)
        done += 1
        if done % 200 == 0:
            print(f"  {done}", file=sys.stderr)

    await asyncio.gather(*(one(cid, rec) for cid, rec in META.items()))
    print(f"updated {done} files")


if __name__ == "__main__":
    asyncio.run(main())
