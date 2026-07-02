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
