// tests/frontend/typewriter-pace.test.mjs
import { strict as assert } from "node:assert";
import test from "node:test";
import { drainCount } from "../../frontend/progressive-md.js";

test("backlog 小 → 一次吐 1 字（保留打字感）", () => {
  assert.equal(drainCount(1), 1);
  assert.equal(drainCount(20), 1);
});

test("backlog 中 → 加速", () => {
  assert.ok(drainCount(120) >= 2);
});

test("backlog 大 → 更快但有上限", () => {
  const c = drainCount(2000);
  assert.ok(c >= 4 && c <= 8);   // 有上限，不一口氣噴完（保留動感）
});
