"""把人工確認的系所 override 套進 dept_mapping.json（可重現）。
用法：python scripts/apply_dept_overrides.py
- overrides 檔 backend/dept_mapping_overrides.json：{substring: canonical_dept}
- 對 dept_mapping.json 每個 key，若含某 substring → 設 dept_canonical/college/confidence=high/source=human_override。
重跑 build_dept_mapping.py 後務必再跑這支，才不會遺失人工決策。"""
from __future__ import annotations
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.dept_vocab import DEPT_TO_COLLEGE

ROOT = Path(__file__).resolve().parent.parent
MAP = ROOT / "backend/dept_mapping.json"
OVR = ROOT / "backend/dept_mapping_overrides.json"


def apply_overrides(mapping: dict, overrides: dict) -> list[tuple[str, str, str]]:
    """就地套用；回傳 (dirty, before, after) 清單（僅實際改動）。"""
    changed = []
    for k in list(mapping):
        for sub, canon in overrides.items():
            if sub in k:
                before = mapping[k].get("dept_canonical", "")
                mapping[k] = {
                    "dept_canonical": canon,
                    "college": DEPT_TO_COLLEGE.get(canon, ""),
                    "confidence": "high",
                    "source": "human_override",
                }
                if before != canon:
                    changed.append((k, before, canon))
                break
    return changed


def main() -> None:
    mapping = json.loads(MAP.read_text(encoding="utf-8"))
    overrides = json.loads(OVR.read_text(encoding="utf-8"))
    changed = apply_overrides(mapping, overrides)
    MAP.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"套用 {len(overrides)} 條 override，改動 {len(changed)} 個髒值：")
    for k, b, c in changed:
        print(f"  {k!r}: {b} -> {c}")


if __name__ == "__main__":
    main()
