# frontend/e2e — Q&A Playwright E2E 測試

## 兩個 Project

| project | backend port | DATABASE_URL | 測試重點 |
|---------|-------------|--------------|---------|
| `qa-no-db` | 8001 | 空字串（明確清空）→ db.init_pool 早退 → ephemeral in-memory session | Q2(多輪不503)、Q3(reload不崩)、Q4(離題引導)、Q5(XSS) |
| `qa-with-db` | 8002 | 從 `$DATABASE_URL` 環境變數帶入 | Q1(多輪+DB讀回)、Q4、Q5 |

兩個 project 跑同一份 `qa.spec.ts`：
- `qa-with-db` 在 `DATABASE_URL` 未設時全部 **skip（非失敗）**。
- Q3 在 `qa-with-db` 中 skip（reload 遺失只對 ephemeral 有意義）。

---

## 環境前置

```bash
# 安裝 Playwright 與 Chromium（只需一次）
npm i -D @playwright/test
npx playwright install chromium
```

必要環境變數：

```bash
export GEMINI_API_KEY="..."               # 兩個 project 均需（真實 RAG）
export FILE_SEARCH_STORE_NAME="..."       # backend 依賴（fileSearchStores/...）
# qa-with-db 才需要：
export DATABASE_URL="postgresql://..."    # Railway DATABASE_PUBLIC_URL
```

---

## 執行指令

```bash
# 在 frontend/ 目錄下執行

# 跑全部（兩 project）
npx playwright test

# 只跑 no-DB（不需 DATABASE_URL，也不燒 DB quota）
npx playwright test --project=qa-no-db

# 只跑 with-DB（需 DATABASE_URL）
DATABASE_URL="$DATABASE_PUBLIC_URL" npx playwright test --project=qa-with-db

# 指定測試名稱
npx playwright test --project=qa-no-db -g "多輪問答"

# 除錯模式（開啟瀏覽器 + slowMo）
npx playwright test --project=qa-no-db --headed --slowmo=300

# 顯示 trace（失敗後用）
npx playwright show-trace test-results/<test>/trace.zip
```

---

## 5x Flake Gate

**關鍵 e2e（Q1/Q2）在 merge 前須通過 5 連綠**，指令帶 `--repeat-each=5`：

```bash
# Q2：no-DB 多輪（不需 DATABASE_URL，本機最快驗）
npx playwright test --project=qa-no-db -g "多輪問答" --repeat-each=5

# Q1：with-DB 多輪 + DB 讀回
DATABASE_URL="$DATABASE_PUBLIC_URL" npx playwright test --project=qa-with-db -g "多輪問答" --repeat-each=5
```

預期：兩輪輸出均 `5 passed`、無 `flaky`。  
未帶 `DATABASE_URL` 時 with-DB 全 skip → 記為「環境未驗」，不擋本機 no-DB gate。

---

## 為何 E2E 樣本刻意稀少？

Q&A 每輪呼叫真實 Gemini File Search（~20-60s/輪），會消耗 API 配額與 RPM。  
策略（見 `docs/superpowers/specs/2026-06-04-test-strategy-fanout-qa.md §1`）：
- **純函式、無 I/O 邏輯** → pytest 單元（多、快、窮舉）。
- **跨邊界關鍵流程**（前端↔backend↔DB↔RAG）→ Playwright e2e（少、貴、關鍵）。
- 禁 mock 自家 `/qa` success path；允許 `page.route fulfill({status:503})` 注入錯誤。
