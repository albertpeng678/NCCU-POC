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


def test_degree_canonical_self_maps():
    # Critical bug（review 實測）：使用者問「歷史系的通識課」，LLM 正確抽出 degree_level 原文
    # 「通識」，但舊版 normalize_degree("通識") 回 None（DEGREE_ALIASES 沒有「通識」自我對映鍵，
    # 也沒有「已是 canonical 就原樣返回」的短路）→ build_dept_filter 誤判成「未指定學制」→
    # 加上 {"type":"ne","key":"degree_level","value":"通識"}，排除通識，與使用者要的完全相反。
    # 同理「學士」對 alias key「學士班」的 fuzz.ratio=80 < cutoff 82，也會靜默漏掉學制條件。
    # normalize_degree 對所有 6 個 canonical degree_level 值都必須原樣返回。
    assert normalize_degree("通識") == "通識"
    assert normalize_degree("學士") == "學士"
    assert normalize_degree("碩士") == "碩士"
    assert normalize_degree("博士") == "博士"
    assert normalize_degree("碩博") == "碩博"


def test_degree_garbage_and_none_returns_none():
    assert normalize_degree(None) is None
    assert normalize_degree("asdfqwer") is None

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


# ───── Task 6 concern #2（false-negative）：_COLLEGES 改從 vocab 衍生，不漏收錄 ─────


def test_college_from_vocab_not_hardcoded_list():
    # 「國際金融學院」在 dept_canonical.json 的 colleges 有這個 key，
    # 但 dept_query.py 舊版 _COLLEGES 是硬編清單、漏收錄它 → normalize_college 誤回 None（false-negative）。
    # 修法：_COLLEGES 改從 load_vocab()["colleges"].keys() 衍生，不會再漏收錄任何 vocab 已有的學院。
    assert normalize_college("國際金融學院") == "國際金融學院"


# ───── Task 6 concern #1：層2 rapidfuzz 子字串包含誤配守門 ─────


def test_layer2_rejects_substring_containment_false_positive():
    # 「護理學院」「管理學院」都不在本校 canonical/alias 中，但 canonical「理學院」是它們的子字串，
    # fuzz.ratio 被子字串包含關係灌到 >= cutoff（85.71）而誤配。子字串包含視為前後綴增減、非錯字，
    # 修法在層2 命中後加守門拒絕，回 None（不誤配、不過濾使用者查詢）。
    assert normalize_college("護理學院") is None
    assert normalize_college("管理學院") is None
    assert normalize_college("人文學院") is None


def test_layer2_substring_guard_does_not_break_real_typo_fix():
    # 守門不可誤傷本來要救的單字替換型錯字：「資訊管里學系」與「資訊管理學系」互不為子字串
    # （中間一字不同，非前後綴增減），仍應正常經層2 rapidfuzz 命中。
    assert normalize_department("資訊管里學系") == "資訊管理學系"
