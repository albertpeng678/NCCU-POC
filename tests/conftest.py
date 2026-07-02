import os
import pytest

# 測試時停用 Sentry：避免測試的例外被送到 Sentry 專案（污染 + 拖慢）。
# 在任何 test 模組 import backend.main 之前執行（conftest 最先載入）；
# load_dotenv(override=False) 不會覆蓋已存在的環境變數，故此設定生效。
os.environ["SENTRY_DSN"] = ""

# 測試時提供 dummy OpenAI key，讓 get_client() 回傳非 None（實際呼叫由各測試 mock 攔截）。
# 沒有這行，Fix 3 的 _require_openai_client() 守門會讓所有 mock-downstream 測試得到 503。
os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy")


@pytest.fixture(autouse=True)
def _dept_filter_off_by_default(monkeypatch):
    """系所感知檢索（Task 7）預設關閉：backend.dept_query.extract_slots 回全 None slots。

    背景：backend.qa.stream_answer 內建的系所分支會呼叫 backend.dept_query.extract_slots，
    該函式內部用 backend.openai_client.get_client()（全域 singleton，而非 stream_answer 呼叫端
    傳入的 fake client）——若不攔截，上面那行 dummy OPENAI_API_KEY 會讓它建立一個「看似可用」的
    真實 AsyncOpenAI client，導致既有（與系所篩選無關的）qa 測試在背景真的打一次 OpenAI API
    （即使失敗會被 extract_slots 自己的 try/except 吞掉、fail-open 成全 None），造成測試意外
    依賴網路、變慢、在無網路 CI 環境變 flaky。
    此 autouse fixture 把它鎖死成 all-None（等同「沒有偵測到系所條件」）→ 既有測試維持純離線、
    行為不變。tests/backend/test_qa_dept_filter.py 需要測系所分支時，會在測試內自行
    monkeypatch.setattr(dept_query, "extract_slots", ...) 覆寫掉這個預設值。
    """
    async def _empty_slots(query):
        return {"department": None, "college": None, "degree_level": None}

    monkeypatch.setattr("backend.dept_query.extract_slots", _empty_slots)


@pytest.fixture(autouse=True)
def _qa_retrieval_off_by_default(monkeypatch):
    """預設受控檢索門面（qa-retr T4）預設關閉：backend.qa.retrieve_and_rerank 回 (None, [])。

    背景與上面 _dept_filter_off_by_default 相同：backend.qa.stream_answer 現在會在 dept 分支
    落空後，預設呼叫 retrieve_and_rerank（backend.qa_retrieval 的門面，門面內部經
    rewrite_to_queries/multi_query_search/rerank_courses 呼叫 backend.openai_client.get_client()
    全域 singleton）。若不攔截，既有（與此檢索改造無關）的 qa 測試會在背景真的打多次 OpenAI API
    （dummy key 會觸發 client 端重試，拖慢/卡住測試、在無網路 CI 變 flaky）。
    此 autouse fixture 把它鎖死成 (None, [])（等同『沒有檢索結果』）→ 既有測試維持純離線、
    落回既有 file_search 路徑，行為不變。需要測這條新路徑的測試會自行
    monkeypatch.setattr("backend.qa.retrieve_and_rerank", ...) 覆寫掉這個預設值。

    ⚠️ 特意 patch backend.qa.retrieve_and_rerank（qa.py 頂層 `from backend.qa_retrieval import
    retrieve_and_rerank` 綁入自己命名空間的那份），而非 backend.qa_retrieval.retrieve_and_rerank
    本身——後者是 tests/backend/test_qa_retrieval.py 直接單元測試的對象，兩份名稱在匯入後已是
    各自獨立的 dict entry，patch 前者不會誤傷後者的單元測試。
    """
    async def _empty_retrieval(question, vs_id):
        return None, []

    monkeypatch.setattr("backend.qa.retrieve_and_rerank", _empty_retrieval)
