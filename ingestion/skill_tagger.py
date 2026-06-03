# ingestion/skill_tagger.py
from __future__ import annotations
import json
import os
from concurrent.futures import ThreadPoolExecutor
from google import genai

BATCH_SIZE = 10  # courses per Gemini call
# 並行 worker 數（Gemini client thread-safe）；可用 INGESTION_WORKERS 覆寫
MAX_WORKERS = int(os.environ.get("INGESTION_WORKERS", "8"))


def chunk_list(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def format_skill_bridge(raw: str) -> str:
    if not raw or not raw.strip():
        return "培養技能：通識素養\n適合職涯：多元職涯\n關鍵詞：學術研究"
    return raw.strip()


def _process_batch(client: genai.Client, batch: list[dict]) -> list[str]:
    """為單一批課程生成 skill bridges；失敗則回 fallback。保證回傳長度 == len(batch)。"""
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
        return [format_skill_bridge(b) for b in bridges]
    except Exception:
        return [
            f"培養技能：通識素養\n適合職涯：多元職涯\n關鍵詞：{c['name']}"
            for c in batch
        ]


def generate_skill_bridges(
    client: genai.Client,
    courses: list[dict],
    max_workers: int = MAX_WORKERS,
) -> list[str]:
    """Batch-generate skill bridge paragraphs，多執行緒並行（client thread-safe）。

    Returns list of skill bridge strings, same order as input.
    """
    batches = chunk_list(courses, BATCH_SIZE)
    all_bridges: list[str] = []
    done = 0
    # ThreadPoolExecutor.map 保證輸出順序對應輸入批次順序
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for batch_bridges in ex.map(lambda b: _process_batch(client, b), batches):
            all_bridges.extend(batch_bridges)
            done += 1
            if done % 20 == 0 or done == len(batches):
                print(f"[skill_tagger]   {done}/{len(batches)} batches done", flush=True)
    return all_bridges
