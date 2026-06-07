// tests/frontend/shiba-progress.test.mjs
// 柴犬等候動畫的純函式核心：階段→旁白對映、done-driven 數字（逼近但不到頂、只 done 才補滿）。
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  stageNarration, easeApproach, fillToDone, stageIndexFromEvent,
  SHIBA_TOTAL, SHIBA_HOLD,
} from "../../frontend/shiba-progress.js";

test("stageNarration 五個真實階段都回有效旁白且語意對", () => {
  assert.ok(stageNarration("understand").now.includes("嗅"));   // 嗅課=檢索前
  assert.ok(stageNarration("retrieve").now.includes("叼"));     // 叼課本=檢索/合併
  assert.ok(stageNarration("compose").now.includes("理由"));    // 歪頭想理由=標註
  assert.ok(stageNarration("finalize").now.includes("分"));     // 甩尾分堆=分組
  const f = stageNarration("filter");
  assert.ok(f && typeof f.now === "string" && f.now.length > 0);
  for (const k of ["understand", "retrieve", "compose", "finalize"]) {
    assert.ok(stageNarration(k).tip.length > 0, `${k} tip 應非空`);
  }
});

test("stageNarration 未知 key 回 fallback（不 throw、不 undefined）", () => {
  const r = stageNarration("__bogus__");
  assert.ok(r && typeof r.now === "string" && r.now.length > 0);
  assert.equal(typeof r.tip, "string");
});

test("easeApproach: 0ms→0、單調遞增、封頂於 holdCount（永不到 total）", () => {
  assert.equal(easeApproach({ elapsedMs: 0 }), 0);
  assert.ok(easeApproach({ elapsedMs: 5000 }) >= easeApproach({ elapsedMs: 1000 }));
  const huge = easeApproach({ elapsedMs: 600000 });
  assert.ok(huge <= SHIBA_HOLD, "封頂在 holdCount");
  assert.ok(huge < SHIBA_TOTAL, "永遠到不了 2718（杜絕數字跑完還在等）");
});

test("easeApproach: 起步低、且整段長等待都在動（不會中途凍住）", () => {
  // 起步低（不要一開始就衝頂）
  assert.ok(easeApproach({ elapsedMs: 5000 }) < SHIBA_HOLD * 0.4);
  // 關鍵：~2 分鐘的等待裡，45s→90s 仍明顯遞增（杜絕「30s 就到頂、剩 90s 凍住」）
  const moveLate = easeApproach({ elapsedMs: 90000 }) - easeApproach({ elapsedMs: 45000 });
  assert.ok(moveLate > 200, `中後段仍要明顯移動，實得 ${moveLate}`);
  // 接近尾段才逼近上限（但仍 < total）
  assert.ok(easeApproach({ elapsedMs: 120000 }) > SHIBA_HOLD * 0.85);
});

test("fillToDone：唯一補滿入口，補到 total，且 > easeApproach 任何時刻", () => {
  assert.equal(fillToDone(2718), 2718);
  assert.equal(fillToDone(), SHIBA_TOTAL);
  assert.ok(fillToDone() > easeApproach({ elapsedMs: 1e9 }));
});

test("stageIndexFromEvent 對齊 (d.n|0)-1，缺 n 回 -1", () => {
  assert.equal(stageIndexFromEvent({ n: 1 }), 0);
  assert.equal(stageIndexFromEvent({ n: 5 }), 4);
  assert.equal(stageIndexFromEvent({ n: 0 }), -1);
  assert.equal(stageIndexFromEvent({}), -1);
});
