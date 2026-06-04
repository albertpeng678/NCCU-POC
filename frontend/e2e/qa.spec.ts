/**
 * frontend/e2e/qa.spec.ts
 *
 * Q&A 多輪對話端對端測試（Q1–Q5）
 * 涵蓋：有 DB / 無 DB 兩環境、多輪記憶、reload 不崩、離題引導、XSS 清洗。
 *
 * 執行前置：
 *   npm i -D @playwright/test
 *   npx playwright install chromium
 *
 * 必要環境變數：
 *   GEMINI_API_KEY          — 兩個 project 均需（打真實 Gemini RAG）
 *   FILE_SEARCH_STORE_NAME  — File Search Store 名稱（backend 依賴）
 *   DATABASE_URL            — 僅 qa-with-db project 需要（Railway DATABASE_PUBLIC_URL）
 *                             未設時 qa-with-db 全部 skip（非失敗）
 *
 * 【注意：本批測試刻意不在此 session 執行，以免燒 Gemini 配額。】
 * Gemini 真實 RAG 每輪 ~20-30s；首輪可達 ~60s（cold start）。
 * timeout 設定反映這個現實：answer 等待 toPass timeout = 120s。
 *
 * 5x Flake Gate（在 CI / 穩定度門檻時帶參數，不寫死進 config）：
 *   npx playwright test --project=qa-no-db  -g "多輪問答" --repeat-each=5
 *   npx playwright test --project=qa-with-db -g "多輪問答" --repeat-each=5
 *   預期：5/5 全綠、0 flake。
 *
 * Locator 策略：
 *   - 模式切換：getByRole("tab") → id="chip-qa" aria-label="自由問答"
 *   - 問題輸入框：getByRole("textbox", { name: /輸入問題/ }) → id="qa-input" aria-label="輸入問題"
 *   - 送出按鈕：getByRole("button", { name: /送出問題/ }) → id="qa-send" aria-label="送出問題"
 *   - 答案區塊：getByTestId("qa-answer") → .bubble.bot[data-testid="qa-answer"]（已加於 app.js）
 *   - Citations：getByTestId("qa-citations") → .cites[data-testid="qa-citations"]（已加於 app.js）
 *   - Session 輪次：getByTestId("qa-session-label") → #qa-session-label（已加 data-testid）
 *
 * 零 waitForTimeout：SSE 逐 token + 打字機用 expect.poll / toPass 輪詢，不用固定 sleep。
 */

import { test, expect, Page, TestInfo } from "@playwright/test";

// ─── 從 project metadata 取 backend URL 與 hasDb 旗標 ──────────────────────
function ctx(testInfo: TestInfo) {
  const m = (testInfo.project.metadata ?? {}) as Record<string, unknown>;
  return {
    backend: (m.backend as string) ?? "http://127.0.0.1:8000",
    hasDb:   (m.hasDb as boolean)  ?? false,
  };
}

// qa-with-db project 但本機未帶 DATABASE_URL → skip（避免假失敗）
function skipIfDbExpectedButMissing(testInfo: TestInfo) {
  const { hasDb } = ctx(testInfo);
  test.skip(
    hasDb && !process.env.DATABASE_URL,
    "DATABASE_URL not set; skip qa-with-db project on this machine",
  );
}

// ─── 共用 helper：切到 Q&A 模式 ──────────────────────────────────────────────
async function switchToQaMode(page: Page) {
  // chip-qa: role="tab", 文字「自由問答」
  await page.getByRole("tab", { name: /自由問答/ }).click();
  // 確認 Q&A 輸入框可見（模式已切換）
  await expect(page.getByRole("textbox", { name: /輸入問題/ })).toBeVisible();
}

// ─── 共用 helper：填問題並送出 ───────────────────────────────────────────────
async function sendQuestion(page: Page, question: string) {
  const input = page.getByRole("textbox", { name: /輸入問題/ });
  await input.fill(question);
  await page.getByRole("button", { name: /送出問題/ }).click();
}

// ─── 共用 helper：等待第 n 個答案區塊出現文字（1-indexed）─────────────────────
// SSE 打字機：token 逐字推入、結束後 tw.finish() 覆蓋 answer；
// 全程用 toPass / expect.poll 輪詢，零 waitForTimeout。
async function waitForAnswer(page: Page, nthIndex: number, timeoutMs = 120_000) {
  // nth(0) = 第1個，nth(1) = 第2個，以此類推
  await expect.poll(
    async () => {
      const el = page.getByTestId("qa-answer").nth(nthIndex);
      return (await el.textContent())?.trim().length ?? 0;
    },
    {
      message: `等待第 ${nthIndex + 1} 個 qa-answer 有內容`,
      timeout: timeoutMs,
      intervals: [500, 1000, 2000],
    },
  ).toBeGreaterThan(10);
}

// ────────────────────────────────────────────────────────────────────────────
// 測試群組
// ────────────────────────────────────────────────────────────────────────────

test.describe("Q&A 多輪（@critical）", () => {
  // 每測前：注入 backend URL 覆蓋、載入頁面、切 Q&A 模式
  test.beforeEach(async ({ page }, testInfo) => {
    skipIfDbExpectedButMissing(testInfo);

    const { backend } = ctx(testInfo);

    // 注入 window.__API_URL__ 讓 app.js 的 CONFIG.API_URL 走對應 backend port
    // app.js 開頭讀 window.__API_URL__（若存在）覆蓋 CONFIG.API_URL
    await page.addInitScript((url: string) => {
      (window as Record<string, unknown>).__API_URL__ = url;
    }, backend);

    await page.goto("/index.html");
    await switchToQaMode(page);
  });

  // ─────────────────────────────────────────────────────────────────────────
  // Q1（qa-with-db）/ Q2（qa-no-db）：同一份多輪流程，兩個 project 各跑一次
  // Q1 測重點：DB 持久化、GET /qa/session/{id} 讀回 turns ≥2
  // Q2 測重點：無 DB 仍不 503、ephemeral session 多輪記憶正常
  // ─────────────────────────────────────────────────────────────────────────
  test("多輪問答：問→答→追問，不 503，session 與 turn 遞增", async ({ page }, testInfo) => {
    const { backend, hasDb } = ctx(testInfo);

    // 監看所有含 /qa 的回應狀態碼（包含 /qa 與 /qa/stream）
    const responseStatuses: number[] = [];
    page.on("response", (r) => {
      if (r.url().includes("/qa")) {
        responseStatuses.push(r.status());
      }
    });

    // ── 第一輪問答 ──────────────────────────────────────────────────────────
    await sendQuestion(page, "資料科學要修哪些課？");
    await waitForAnswer(page, 0);

    // 至少有 citations 區塊渲染（citations 可能為空，但區塊本身要可見）
    // citations 在 bubble 收到 done 事件後才 attachCitesAndFollowups → 用 toBeVisible 等
    await expect(page.getByTestId("qa-citations").first()).toBeVisible({ timeout: 30_000 });

    // session label 顯示「第 1 輪對話」
    await expect(page.getByTestId("qa-session-label")).toContainText(/第 1 輪/, { timeout: 10_000 });

    // ── 第二輪追問（沿用同 session）──────────────────────────────────────────
    await sendQuestion(page, "那統計呢？");
    await waitForAnswer(page, 1);

    // session label 更新到第 2 輪
    await expect(page.getByTestId("qa-session-label")).toContainText(/第 2 輪/, { timeout: 30_000 });

    // ── 全程沒有 503 ──────────────────────────────────────────────────────────
    expect(responseStatuses).not.toContain(503);
    expect(responseStatuses.length).toBeGreaterThan(0);

    // ── with-DB 專屬：讀回持久化 session（turns ≥ 2）────────────────────────
    if (hasDb) {
      // window.__lastQaSessionId__ 由 app.js done 事件後記錄（見 app.js 說明）
      const sid = await page.evaluate(() => (window as Record<string, unknown>).__lastQaSessionId__);
      expect(sid, "with-DB：session_id 應存在").toBeTruthy();

      const r = await page.request.get(`${backend}/qa/session/${sid}`);
      expect(r.ok(), `GET /qa/session/${sid} 應 200`).toBeTruthy();
      const body = await r.json();
      expect(
        Array.isArray(body.turns) && body.turns.length,
        "DB session 應有 ≥2 turns",
      ).toBeGreaterThanOrEqual(2);
    }
  });

  // ─────────────────────────────────────────────────────────────────────────
  // Q3（no-DB only）：reload 後 ephemeral session 遺失可接受，不崩
  // ─────────────────────────────────────────────────────────────────────────
  test("no-DB reload：ephemeral 遺失不崩，可重新提問", async ({ page }, testInfo) => {
    const { hasDb } = ctx(testInfo);
    test.skip(hasDb, "reload-loss 測試僅適用 no-DB（with-DB 應保留 session）");

    // 先問一輪
    await sendQuestion(page, "企業管理學什麼？");
    await waitForAnswer(page, 0);

    // reload → session 遺失（ephemeral，符合預期）
    await page.reload();

    // reload 後切回 Q&A
    await switchToQaMode(page);

    // 收集 console error（reload 後不能有 null.classList / EventSource / undefined 例外）
    const consoleErrors: string[] = [];
    page.on("console", (m) => {
      if (m.type() === "error") consoleErrors.push(m.text());
    });

    // 重新提問 → 不殘留、不崩、能正常回答
    await sendQuestion(page, "行銷學主要教什麼？");
    await waitForAnswer(page, 0);

    expect(
      consoleErrors.join("\n"),
      "reload 後不應有 null.classList / EventSource / undefined 等 JS 例外",
    ).not.toMatch(/classList|EventSource|undefined is not/i);
  });

  // ─────────────────────────────────────────────────────────────────────────
  // Q4（both）：離題問題 → 溫和引導，不杜撰課程（防幻覺）
  // ─────────────────────────────────────────────────────────────────────────
  test("離題問題 → 溫和引導 / 防幻覺（不杜撰課程）", async ({ page }) => {
    await sendQuestion(page, "今天台北天氣如何？");
    await waitForAnswer(page, 0);

    const text = (await page.getByTestId("qa-answer").first().textContent()) ?? "";

    // 離題：答案要嘛引導回課程、要嘛誠實查無
    // 不得列出看似真實的課綱連結（即 citations 空 → 防幻覺覆寫）
    expect(
      text,
      "離題問題應含引導回課程或查無的文字",
    ).toMatch(/課程|課綱|問問|查無|協助|推薦|建議|引導/);

    // citations 空時防幻覺機制：答案不應包含「找到以下課程」這類虛構列表
    expect(text).not.toMatch(/以下課程.{0,20}符合/);
  });

  // ─────────────────────────────────────────────────────────────────────────
  // Q5（both）：markdown XSS 清洗 — <script> 不執行，DOMPurify 有作用
  // ─────────────────────────────────────────────────────────────────────────
  test("XSS：問題含 <script> 不被執行，answer 區無 script 標籤", async ({ page }) => {
    // 監聽 dialog（alert/confirm/prompt）
    let dialogFired = false;
    page.on("dialog", async (d) => {
      dialogFired = true;
      await d.dismiss();
    });

    await sendQuestion(page, "<script>window.__xss__=1</script> 政治學如何？");
    await waitForAnswer(page, 0);

    // 1. answer 區內部無 <script> 元素（DOMPurify 已清除）
    const scriptCount = await page.locator('[data-testid="qa-answer"] script').count();
    expect(scriptCount, "answer 區內不得有 <script> 元素").toBe(0);

    // 2. 注入旗標未被設定（window.__xss__ 應 undefined）
    const xssFlag = await page.evaluate(() => (window as Record<string, unknown>).__xss__);
    expect(xssFlag, "window.__xss__ 不應被設定（XSS 注入失敗）").toBeUndefined();

    // 3. 無 alert/confirm/prompt 彈出
    expect(dialogFired, "不應有 dialog 彈出").toBe(false);
  });
});
