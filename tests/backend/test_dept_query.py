from backend.dept_query import normalize_department, normalize_college, normalize_degree

def test_alias_exact():
    assert normalize_department("歷史系") == "歷史學系"
    assert normalize_department("資管") == "資訊管理學系"

def test_already_canonical():
    assert normalize_department("歷史學系") == "歷史學系"

def test_fuzzy_typo():
    assert normalize_college("文苑") == "文學院"      # 錯字 → 最近鄰
    assert normalize_college("文院") == "文學院"      # 別名

def test_degree_alias():
    assert normalize_degree("大學部") == "學士"
    assert normalize_degree("研究所") == "研究所"
    assert normalize_degree("碩士班") == "碩士"

def test_garbage_returns_none():
    assert normalize_department("asdfqwer") is None
    assert normalize_department(None) is None
