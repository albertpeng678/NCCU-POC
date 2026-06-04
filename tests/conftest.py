import os

# 測試時停用 Sentry：避免測試的例外被送到 Sentry 專案（污染 + 拖慢）。
# 在任何 test 模組 import backend.main 之前執行（conftest 最先載入）；
# load_dotenv(override=False) 不會覆蓋已存在的環境變數，故此設定生效。
os.environ["SENTRY_DSN"] = ""
