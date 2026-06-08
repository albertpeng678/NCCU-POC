import json, re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def test_100_careers_and_categories_consistent():
    skills=json.loads((ROOT/"backend/career_skills.json").read_text(encoding="utf-8"))
    cats=json.loads((ROOT/"backend/career_categories.json").read_text(encoding="utf-8"))
    assert len(skills)==100, f"career_skills 應 100，實際 {len(skills)}"
    # 每職涯有非空 skills
    for k,v in skills.items(): assert v.get("skills"), f"{k} 缺 skills"
    # 分類涵蓋全部、無重複、無多餘
    flat=[c for arr in cats.values() for c in arr]
    assert len(flat)==len(set(flat)), "分類有重複職涯"
    assert set(flat)==set(skills.keys()), "分類與 career_skills 不一致（漏/多）"
    assert 8<=len(cats)<=10, "職能大類 8-10 個"
