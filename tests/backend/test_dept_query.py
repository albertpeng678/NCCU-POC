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


def test_pinyin_layer_does_not_false_positive_on_common_suffix():
    # Critical bug（review 抓到）：拼音比對若把「學系/學院」這種通用字尾也算進相似度，
    # 會讓短查詢被共同字尾灌分，誤配到不相關的系所/學院。
    # 修法：拼音比對前先砍通用單位字尾、只比「核心」，核心拼音須精確相等。
    assert normalize_department("數學系") is None      # 原誤配 "社會學系"（fuzz 84.21）
    assert normalize_college("醫學院") is None          # 原誤配 "理學院"（88.89）
    assert normalize_college("體育學院") is None        # 原誤配 "教育學院"（83.33）
    assert normalize_college("海洋學院") is None        # 原誤配 "商學院"（84.62）


def test_pinyin_layer_still_catches_homophones_after_core_fix():
    # 修法不可誤傷本來要救的同音字案例。
    assert normalize_department("立是系") == "歷史學系"
    assert normalize_department("心裡系") == "心理學系"


def test_broad_scan_no_false_positive_on_real_sounding_names():
    # 廣掃一批「聽起來像真系所/學院但非本校 canonical」的名稱，確認不會被拼音層誤配。
    assert normalize_department("音樂系") is None
    assert normalize_department("電機系") is None
    assert normalize_department("機械系") is None
    assert normalize_college("藝術學院") is None
    # 「外文系」核心「外文」與本校真實特別單位「外文中心」的核心（砍「中心」後）字面完全相同
    # （非拼音灌水巧合），屬合理對映，非誤配。
    assert normalize_department("外文系") == "外文中心"
