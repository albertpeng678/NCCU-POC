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
