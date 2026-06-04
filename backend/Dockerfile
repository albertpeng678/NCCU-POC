FROM python:3.11-slim
WORKDIR /app
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ ./backend/
EXPOSE 8000
# shell form（非 JSON array）→ 透過 /bin/sh -c 執行 → ${PORT} 會被展開。
# Railway 注入 PORT；本地無 PORT 時退回 8000。
CMD uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}
