"""稽核 dept_mapping.json：覆蓋率、每 canonical 桶課數、落『其他』清單、低信心清單。
用法：python scripts/audit_dept_mapping.py"""
from __future__ import annotations
import json
import collections
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
meta = json.loads((ROOT / "backend/courses_meta.json").read_text(encoding="utf-8"))
mapping = json.loads((ROOT / "backend/dept_mapping.json").read_text(encoding="utf-8"))

total = len(meta)
mapped = other = 0
bucket = collections.Counter()
other_vals = collections.Counter()
low = set()

for v in meta.values():
    d = v.get("department", "")
    m = mapping.get(d)
    if not m or m["dept_canonical"] == "其他":
        other += 1
        other_vals[d] += 1
    else:
        mapped += 1
        bucket[m["dept_canonical"]] += 1
    if m and m.get("confidence") == "low":
        low.add(d)

print(f"覆蓋：{mapped}/{total}（{mapped/total*100:.1f}%）；落『其他』：{other} 門")
print(f"\n桶大小前 15：")
for dept, n in bucket.most_common(15):
    print(f"  {dept:<20} {n}")
big = [d for d, n in bucket.items() if n > 150]
print(f"\n異常肥大桶（>150 課，疑誤併）：{big or '無'}")
print(f"\n落『其他』髒值（{len(other_vals)} 種；應為院共同/校選修/遠距）：")
for d, n in other_vals.most_common():
    print(f"  {d!r}: {n} 課")
print(f"\n低信心髒值（{len(low)} 種）：{sorted(low)}")
