// frontend/playwright.config.ts
//
// 執行前需要：
//   npm i -D @playwright/test
//   npx playwright install chromium
//
// 需要環境變數：
//   GEMINI_API_KEY        — 兩個 project 共用（後端 RAG 真實呼叫）
//   DATABASE_URL          — 僅 qa-with-db project 需要（Railway DATABASE_PUBLIC_URL）
//                           未設時 qa-with-db 的測試會 skip（非失敗）
//
// 5x Flake gate（不寫死進 config，在 CLI 帶參數）：
//   npx playwright test --project=qa-no-db  -g "多輪問答" --repeat-each=5
//   npx playwright test --project=qa-with-db -g "多輪問答" --repeat-each=5

import { defineConfig, devices } from "@playwright/test";

const BACKEND_NO_DB  = "http://127.0.0.1:8001";
const BACKEND_WITH_DB = "http://127.0.0.1:8002";
const FRONTEND       = "http://127.0.0.1:3000";

// qa-with-db project 需要 DATABASE_URL（本機用 Railway DATABASE_PUBLIC_URL，由環境變數帶入）
const DB_URL = process.env.DATABASE_URL ?? "";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: [["list"]],
  use: {
    trace: "on-first-retry",
    actionTimeout: 0,
    // 降低動畫干擾、讓 DOM 狀態更穩定
    reducedMotion: "reduce",
  },
  projects: [
    {
      name: "qa-no-db",
      testMatch: /qa\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        baseURL: FRONTEND,
      },
      // 測試從 testInfo.project.metadata 讀取 backend URL 與 hasDb 旗標
      metadata: { backend: BACKEND_NO_DB, hasDb: false },
    },
    {
      name: "qa-with-db",
      testMatch: /qa\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        baseURL: FRONTEND,
      },
      metadata: { backend: BACKEND_WITH_DB, hasDb: true },
    },
  ],
  webServer: [
    {
      // 無 DB backend：明確不帶 DATABASE_URL → db.init_pool 早退 → ephemeral in-memory session
      command:
        "GEMINI_API_KEY=${GEMINI_API_KEY} ALLOWED_ORIGIN=* python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001",
      url: `${BACKEND_NO_DB}/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      cwd: "..",
      env: {
        GEMINI_API_KEY: process.env.GEMINI_API_KEY ?? "",
        ALLOWED_ORIGIN: "*",
        DATABASE_URL: "",   // 明確清空 → 觸發 db.init_pool 早退
        FILE_SEARCH_STORE_NAME: process.env.FILE_SEARCH_STORE_NAME ?? "",
      },
    },
    {
      // 有 DB backend：帶 DATABASE_URL（缺省時 server 仍起，但 qa-with-db 的測試會 skip）
      command:
        "GEMINI_API_KEY=${GEMINI_API_KEY} ALLOWED_ORIGIN=* python -m uvicorn backend.main:app --host 127.0.0.1 --port 8002",
      url: `${BACKEND_WITH_DB}/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      cwd: "..",
      env: {
        GEMINI_API_KEY: process.env.GEMINI_API_KEY ?? "",
        ALLOWED_ORIGIN: "*",
        DATABASE_URL: DB_URL,
        FILE_SEARCH_STORE_NAME: process.env.FILE_SEARCH_STORE_NAME ?? "",
      },
    },
    {
      // 前端靜態伺服器
      command: "python -m http.server 3000",
      url: FRONTEND,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      cwd: ".",
    },
  ],
});
