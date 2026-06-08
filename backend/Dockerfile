FROM python:3.11-slim
WORKDIR /app
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ ./backend/
# 同源前端：整個 frontend/ 放進 image，由 backend StaticFiles 服務。
# 刻意複製整包（非寫死清單）：寫死清單曾漏掉 Session 7 新增的 shiba-progress.js/qa-recovery.js/
# end-state.js/shiba.json → 線上 404 → app.js import 失敗 → 全站死。整包複製根治「新增前端檔漏 deploy」。
# 多帶的 e2e/mockup/nginx 範本是純靜態惰性檔，無人請求、無害。
COPY frontend/ ./frontend/
# 一次性維護腳本（career_budget 離線灌庫等）放進映像，供 `railway ssh` 在容器內用內網 DB 執行
# （本機網路常擋 Railway TCP proxy port → 從容器內走內網是可靠路徑）。git 只追蹤必要腳本，惰性無害。
COPY scripts/ ./scripts/
EXPOSE 8000
# shell form（非 JSON array）→ 透過 /bin/sh -c 執行 → ${PORT} 會被展開。
# Railway 注入 PORT；本地無 PORT 時退回 8000。
CMD uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}
