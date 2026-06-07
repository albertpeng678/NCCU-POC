// tests/frontend/qa-recovery.test.mjs
// 問答失敗復原純邏輯：error_type → UI 狀態（暫時性過載→可重試 vs 查無資料→引導換問法）。
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  qaErrorUiState, buildRetryState, noMatchChips,
  TRANSIENT_MSG, NO_MATCH_MSG,
} from "../../frontend/qa-recovery.js";

test("qaErrorUiState：暫時性過載可重試、查無資料不可重試", () => {
  assert.deepEqual(qaErrorUiState("rate_limited"), { kind: "transient", retryable: true });
  assert.deepEqual(qaErrorUiState("timeout"), { kind: "transient", retryable: true });
  assert.deepEqual(qaErrorUiState("no_match"), { kind: "no_match", retryable: false });
});

test("qaErrorUiState：unknown 與亂值偏向可重試（鼓勵再試）", () => {
  assert.deepEqual(qaErrorUiState("unknown"), { kind: "transient", retryable: true });
  assert.deepEqual(qaErrorUiState(undefined), { kind: "transient", retryable: true });
  assert.deepEqual(qaErrorUiState("xyz"), { kind: "transient", retryable: true });
});

test("buildRetryState：回填原問題、按鈕文案", () => {
  const s = buildRetryState("我想當 PM 要修什麼課");
  assert.equal(s.refillValue, "我想當 PM 要修什麼課");
  assert.equal(s.buttonLabel, "重新提問");
  assert.equal(s.disabledLabel, "重試中…");
  assert.equal(buildRetryState("").refillValue, "");
});

test("noMatchChips：有 followup 用之，空則回預設方向（非空、無重複）", () => {
  assert.deepEqual(noMatchChips(["要不要看國際關係？"]), ["要不要看國際關係？"]);
  const def = noMatchChips([]);
  assert.ok(Array.isArray(def) && def.length >= 2);
  const defNull = noMatchChips(undefined);
  assert.ok(Array.isArray(defNull) && defNull.length >= 2);
});

test("文案常數非空（集中管理、繁中）", () => {
  assert.ok(typeof TRANSIENT_MSG === "string" && TRANSIENT_MSG.length > 0);
  assert.ok(typeof NO_MATCH_MSG === "string" && NO_MATCH_MSG.length > 0);
});
