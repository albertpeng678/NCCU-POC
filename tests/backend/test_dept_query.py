from backend.dept_query import normalize_department, normalize_college, normalize_degree

def test_alias_exact():
    assert normalize_department("歷史系") == "歷史學系"
    assert normalize_department("資管") == "資訊管理學系"

def test_already_canonical():
    assert normalize_department("歷史學系") == "歷史學系"

def test_alias_typo_via_college_alias_table():
    # 「文苑」「文院」都命中 college_aliases 硬編別名（層1b 快路），不會進到層2 rapidfuzz。
    assert normalize_college("文苑") == "文學院"
    assert normalize_college("文院") == "文學院"      # 別名

def test_fuzzy_typo_via_rapidfuzz_layer():
    # 「資訊管里學系」不在任何別名表，必須真正經過層2 rapidfuzz 最近鄰治錯字（里→理）。
    # fuzz.ratio("資訊管里學系", "資訊管理學系") ≈ 83.33，>= _FUZZ_CUTOFF(82)，真模糊命中。
    assert normalize_department("資訊管里學系") == "資訊管理學系"

def test_degree_alias():
    assert normalize_degree("大學部") == "學士"
    assert normalize_degree("研究所") == "研究所"
    assert normalize_degree("碩士班") == "碩士"

def test_garbage_returns_none():
    assert normalize_department("asdfqwer") is None
    assert normalize_department(None) is None

def test_short_or_junk_strings_never_guessed():
    # F3: fuzz.WRatio 的 partial-match 特性會讓短字串/亂打字串被誤配到不相關的系所/學院
    # （例如「中」被灌水配到「華語文教學中心」、「商院子」被配到「商學院」）。
    # 對不上必須回 None，不可亂猜——否則會誤過濾使用者查詢。
    assert normalize_college("中") is None
    assert normalize_department("法") is None
    assert normalize_college("商院子") is None


def test_pinyin_layer_catches_homophone_typos():
    # 層4：拼音比對治同音字（rapidfuzz 字形比對抓不到，因字形完全無關）。
    # 「立是系」拼音 lishixi，「歷史系」（alias key）拼音同為 lishixi → 100% 命中。
    assert normalize_department("立是系") == "歷史學系"
    # 「心裡系」拼音 xinlixi vs 「心理學系」拼音 xinlixuexi → fuzz.ratio ≈ 82.35，命中。
    assert normalize_department("心裡系") == "心理學系"


def test_pinyin_layer_still_rejects_garbage():
    # 拼音層不可把守門變鬆：短字串/無關字串仍須回 None。
    assert normalize_department("法") is None
    assert normalize_college("商院子") is None
    assert normalize_department("asdfqwer") is None
    assert normalize_college("中") is None
