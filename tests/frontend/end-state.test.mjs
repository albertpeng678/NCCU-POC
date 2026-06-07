// tests/frontend/end-state.test.mjs
// 翻到底「看完了」end-state：柴犬把課都叼來了 + 三個下一步（不再叫 AI）。
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildEndStateHtml } from "../../frontend/end-state.js";

test("end-state 含柴犬文案 + 看完了語氣", () => {
  const html = buildEndStateHtml({ career: "產品經理(PM)" });
  assert.ok(/都叼來了|看完了/.test(html));
  assert.ok(html.includes("產品經理(PM)"));
});

test("end-state 含三個下一步 data-action", () => {
  const html = buildEndStateHtml({ career: "x" });
  assert.ok(html.includes('data-action="new-career"'));
  assert.ok(html.includes('data-action="to-qa"'));
  assert.ok(html.includes('data-action="rewatch"'));
});

test("end-state escape career（防 XSS）", () => {
  const html = buildEndStateHtml({ career: '<img src=x onerror=alert(1)>' });
  assert.ok(!html.includes("<img"));
  assert.ok(html.includes("&lt;img"));
});

test("end-state career 缺值不崩", () => {
  const html = buildEndStateHtml({});
  assert.ok(typeof html === "string" && html.length > 0);
});
