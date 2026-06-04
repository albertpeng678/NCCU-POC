FROM python:3.11-slim
WORKDIR /app
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ ./backend/
# 同源前端：把前端靜態檔放進 image，由 backend StaticFiles 服務（只拿需要的，不含 e2e/Dockerfile）
COPY frontend/index.html frontend/style.css frontend/app.js frontend/careers.js frontend/pagination.js frontend/progressive-md.js ./frontend/
EXPOSE 8000
# shell form（非 JSON array）→ 透過 /bin/sh -c 執行 → ${PORT} 會被展開。
# Railway 注入 PORT；本地無 PORT 時退回 8000。
CMD uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}
