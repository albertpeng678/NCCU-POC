import os

# 測試時停用 Sentry：避免測試的例外被送到 Sentry 專案（污染 + 拖慢）。
# 在任何 test 模組 import backend.main 之前執行（conftest 最先載入）；
# load_dotenv(override=False) 不會覆蓋已存在的環境變數，故此設定生效。
os.environ["SENTRY_DSN"] = ""

# 測試時提供 dummy OpenAI key，讓 get_client() 回傳非 None（實際呼叫由各測試 mock 攔截）。
# 沒有這行，Fix 3 的 _require_openai_client() 守門會讓所有 mock-downstream 測試得到 503。
os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy")
