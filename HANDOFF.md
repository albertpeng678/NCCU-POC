# NCCU 課程推薦系統 — 交接文件（HANDOFF.md）

> 專案進展史 + WBS + 待辦。給接手的 agent 快速掌握「做到哪、還剩什麼」。
> 最後更新：2026-06-03

---

## 一、目前狀態總覽

| 模組 | 程式碼 | 測試 | 真實端到端驗證 | 部署 |
|------|:---:|:---:|:---:|:---:|
| Ingestion（知識庫建置） | ✅ | ✅ 15 | ✅ 5課 dry-run | — |
| Backend 職涯推薦 | ✅ | ✅ | ✅ 真實 Gemini | ⬜ |
| Backend Logging + Judge | ✅ | ✅ | ⬜ 待 live Postgres | ⬜ |
| Backend Q&A 問答 | ✅ | ✅ | ✅ 單輪+多輪+citations | ⬜ |
| Frontend 推薦模式 | ✅ | — | ✅ 桌面+mobile | ⬜ |
| Frontend Q&A 模式 | ✅ | — | 🟡 進行中（剛開始 Playwright 測） | ⬜ |

**測試總計：49 passing**（ingestion 15 + backend 34）。

**本地驗證環境**：Docker Postgres 容器 `nccu-pg`（`postgresql://postgres:nccu@127.0.0.1:5440/nccu`，已套 schema.sql）。dry-run Gemini File Search Store：`fileSearchStores/nccucourses1142-1ie5gitqgtur`（僅 5 課：政治/社會/個經×3）。

---

## 二、進展史（時間序）

1. **Brainstorming → Spec**：兩份設計文件（推薦系統、Q&A 模式）於 `docs/superpowers/specs/`。
2. **研究確認資料源**：用 Playwright 探勘政大網站 → 確認 `CoursesList.xlsx`（2877課）+ 課綱 URL 公式 + SSL legacy 需求。
3. **Plan 1 Ingestion**（commits eb124dc→a692d9f）：xlsx_parser/scraper/skill_tagger/uploader/run.py。15 tests。5 課 dry-run 端到端成功，產生 dry-run store + courses_meta.json。**過程修 3 個 SDK bug**（create config、import_file poll、SSL）。
4. **Plan 2 Backend 推薦**（fe22adb→567db2d）：models/recommend/judge/main/Dockerfile。26 tests。真實 Gemini /recommend 驗證（公務員→3課分組）。**修 stage1 file_search+json_mime 衝突**。
5. **Plan 4 Logging+Judge**（b98fb97→3e966b4）：db/logger/judge + 兩階段背景 logging + analytics.sql。
6. **Plan 3 Frontend 推薦**（9454925）：navy glassmorphism SPA。桌面+mobile offcanvas Playwright 驗證。**修 `[hidden]` loading bug**。同時把後端 reason 改結構化（lead+points）。
7. **Q&A 模式**（2472970→0c49378）：
   - Spec（含 NNgroup 模式可見性、RAGAS judge、followup chips 研究）
   - 並行實作（dispatch 後端 agent + 主線做前端）：qa/qa_judge/qa_logger + 雙模式前端 + main.py 整合
   - **修 interactions API outputs 解析 bug**（扁平 list、annotation source 萃 course_id）
   - 真實驗證：單輪✅ 多輪✅（previous_interaction_id 上下文接續）citations✅ followup✅

---

## 三、WBS（工作分解 + 狀態）

### ✅ 已完成

- [x] 1. Ingestion pipeline（程式碼 + 15 tests + dry-run）
- [x] 2. Backend 推薦 API（/recommend，26 tests，真實驗證）
- [x] 3. Backend Logging + 推薦 Judge（程式碼 + tests）
- [x] 4. Backend Q&A（/qa /qa/session，qa tests，真實單輪+多輪驗證）
- [x] 5. Frontend 推薦模式（桌面+mobile Playwright）
- [x] 6. Frontend Q&A 模式（雙模式 UI 程式碼完成）
- [x] 7. 後端整合（main.py /qa endpoint + models）
- [x] 8. CLAUDE.md + HANDOFF.md

### 🟡 進行中

- [ ] 9. **Frontend Q&A 端到端 Playwright 驗證**
  - 已完成：啟動 backend（接 5440 DB）、frontend（:3000）、切到問答模式
  - 待做：實測「輸入問題→送出→loading→答案氣泡+citations+followup chips 渲染」、多輪追問、新對話、模式切換不互相污染
  - 狀態：剛點完 `#chip-qa` 切換，尚未送出問題

### ⬜ 待完成（依優先序）

- [ ] 10. **剩餘推薦模式 E2E**（Plan 3 task #36/#37）
  - tablet 768 響應式截圖
  - 鍵盤導航（Tab/方向鍵/Enter/Esc）
  - 錯誤狀態（503 顯示）
- [ ] 11. **Full ingestion run**（2877 課）
  - 指令：`cd C:/side/NCCU-poc && python -m ingestion.run`（不帶 INGESTION_LIMIT）
  - 耗時 30-90 分，消耗 Gemini quota，free tier 可能撞 rate limit
  - 完成後更新 `.env` 與 Railway 的 `FILE_SEARCH_STORE_NAME`，並 commit 新 courses_meta.json
- [ ] 12. **Railway Postgres provisioning**
  - dashboard 加 Postgres service → 取得 DATABASE_URL
  - 在 Railway Postgres Query tab 跑 `backend/schema.sql`
- [ ] 13. **Railway 部署**（依 deploy 順序，見下）
- [ ] 14. **Q&A 模式跨裝置 E2E**（mobile/tablet 對話氣泡、composer）
- [ ] 15.（可選）Q&A 重整讀回 GET /qa/session 前端串接（目前後端有，前端未串）

---

## 四、Railway 部署順序（避免環境變數 chicken-and-egg）

1. 本地跑 `ingestion/run.py` → 取得 `FILE_SEARCH_STORE_NAME`
2. Railway 加 Postgres → 跑 schema.sql → 取得 `DATABASE_URL`
3. 部署 backend（先設 `ALLOWED_ORIGIN=*`）→ 取得 backend URL
4. 把 backend URL 填入 `frontend/app.js` 的 `CONFIG.API_URL`
5. 部署 frontend → 取得 frontend URL
6. backend `ALLOWED_ORIGIN` 改為 frontend URL → redeploy

---

## 五、新機器從零設定（git clone 後完整步驟）

> 適用：在另一台機器 clone 此 repo 後，要跑起本地驗證環境。
> repo 已含 `backend/courses_meta.json`（5 課 dry-run 版）。**未含** `.env`（gitignored）、Python 套件、Postgres。

### 前置需求
- Python 3.11+、Docker、git
- **同一把 `GEMINI_API_KEY`**（dry-run File Search Store 綁在原帳號雲端，換 key 就存取不到，會回 503）

### 步驟

```bash
# 0. clone（用 github-personal key）
git clone git@github.com:albertpeng678/NCCU-POC.git && cd NCCU-POC

# 1. 安裝依賴
pip install -r backend/requirements.txt
pip install -r ingestion/requirements.txt   # 若要跑 ingestion

# 2. 建 .env（從 example 複製後填值）
cp .env.example .env
#   填入：
#   GEMINI_API_KEY=<與原機器同一把，才能存取既有 dry-run store>
#   FILE_SEARCH_STORE_NAME=fileSearchStores/nccucourses1142-1ie5gitqgtur   # dry-run 5課；full run 後換新值
#   ALLOWED_ORIGIN=*

# 3. 本地 Postgres（容器名自取，埠 5440 與下方一致即可）
docker run -d --name nccu-pg -e POSTGRES_PASSWORD=nccu -e POSTGRES_DB=nccu -p 5440:5432 postgres:16-alpine
#   等就緒後套 schema：
docker exec -i nccu-pg psql -U postgres -d nccu < backend/schema.sql

# 4. 全測試（不需 DB/Gemini，純單元測試）
python -m pytest tests/ -q          # 預期 49 passed

# 5. 啟動 backend（DATABASE_URL 用環境變數傳，勿寫進 .env 以免覆蓋部署設定）
DATABASE_URL="postgresql://postgres:nccu@127.0.0.1:5440/nccu" python -m uvicorn backend.main:app --port 8000
#   健康檢查：curl http://localhost:8000/health → {"status":"ok"}

# 6. 啟動 frontend
cd frontend && python -m http.server 3000
#   開 http://localhost:3000/index.html
```

### 驗證可用性（dry-run store 限政治/社會/經濟相關）
```bash
# 推薦模式（會 match 的職涯）
curl -s -X POST http://localhost:8000/recommend -H "Content-Type: application/json" \
  --data-binary "{\"career\": \"公務員\"}"
# 問答模式
curl -s -X POST http://localhost:8000/qa -H "Content-Type: application/json" \
  --data-binary "{\"question\": \"政治學在教什麼?\", \"session_id\": null}"
```

> ⚠️ 新機器若 **沒有原 GEMINI_API_KEY**：無法用既有 dry-run store。需自己跑一次 ingestion 建新 store（見待辦 #11），或向原作者取得 key。
> ⚠️ Windows 注意：連 Docker Postgres 用 `127.0.0.1`（非 `localhost`，避免 IPv6 `::1` 連線被拒）。

---

## 六、已知限制 / 注意事項

- **dry-run store 只有 5 課**：問答/推薦只在政治/社會/經濟相關問題有結果；其他職涯（如資料科學家）會回 503「無候選課程」。Full ingestion 後才完整。
- **File Search Store 是 Gemini 帳號層級雲端資源**（非本機檔案）：同一把 `GEMINI_API_KEY` 在任何機器都能存取同一個 store，故跨機器驗證只需 .env 填對 key + store name，無需重新 ingestion。換 key = 看不到既有 store。
- **/qa 強依賴 DB**：無 DATABASE_URL 時回 503（與 /recommend 不同，後者可優雅降級）。
- **latency**：兩階段推薦 ~50-80s，問答 ~15-30s（Gemini File Search 本身較慢）。PoC 可接受。
- **courses_meta.json 已 commit**（un-gitignored）：backend runtime 依賴，Railway 從 git build 需要它。
- **caveman hook 已從 ~/.claude/settings.json 移除**（曾導致 Windows 卡頓 + 輸出雜訊）。
