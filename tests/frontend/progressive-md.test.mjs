// tests/frontend/progressive-md.test.mjs
// 漸進 markdown 渲染器的 buffer/節流邏輯（純邏輯，render/schedule 注入，免瀏覽器/marked）。
import test from "node:test";
import assert from "node:assert/strict";
import { createProgressiveRenderer } from "../../frontend/progressive-md.js";

// 同步 scheduler：呼叫即立刻執行（測試用），回傳可被 cancel 的 token
function syncSched(){
  let pending = null;
  return {
    schedule(fn){ pending = fn; return Symbol("t"); },     // 不立刻跑，等 flushNow 觸發
    cancel(){ pending = null; },
    flushNow(){ const f = pending; pending = null; if(f) f(); },
    has(){ return pending !== null; },
  };
}

test("push 累積緩衝，flush 時 render 收到完整累積字串", () => {
  const rendered = [];
  const s = syncSched();
  const r = createProgressiveRenderer({
    render: (buf) => rendered.push(buf),
    schedule: s.schedule, cancel: s.cancel,
  });
  r.push("# 標");
  r.push("題\n\n**粗**");
  assert.equal(rendered.length, 0, "尚未 flush 不該 render");
  s.flushNow();
  assert.deepEqual(rendered, ["# 標題\n\n**粗**"]);
});

test("多次 push 只排程一次（節流），避免每 token 重渲染", () => {
  let scheduleCalls = 0;
  const s = syncSched();
  const r = createProgressiveRenderer({
    render: () => {}, cancel: s.cancel,
    schedule: (fn) => { scheduleCalls++; return s.schedule(fn); },
  });
  r.push("a"); r.push("b"); r.push("c");
  assert.equal(scheduleCalls, 1, "三次 push 應只排程一次（直到 flush）");
  s.flushNow();
  r.push("d");
  assert.equal(scheduleCalls, 2, "flush 後再 push 才重新排程");
});

test("finish：取消待處理排程 + 最終 render + 回呼", () => {
  const rendered = [];
  let cancelled = false;
  const s = syncSched();
  const r = createProgressiveRenderer({
    render: (buf) => rendered.push(buf),
    schedule: s.schedule, cancel: () => { cancelled = true; s.cancel(); },
  });
  r.push("| a | b |\n| - | - |");   // 半截表格（不完整）
  let cbArg = null;
  r.finish((txt) => { cbArg = txt; });
  assert.equal(cancelled, true, "finish 應取消待處理 rAF");
  assert.deepEqual(rendered, ["| a | b |\n| - | - |"], "finish 應做最終 render");
  assert.equal(cbArg, "| a | b |\n| - | - |", "回呼收到完整文字");
});

test("getText 回傳累積緩衝；finish 後 push 被忽略", () => {
  const s = syncSched();
  const r = createProgressiveRenderer({ render: () => {}, schedule: s.schedule, cancel: s.cancel });
  r.push("hello");
  assert.equal(r.getText(), "hello");
  r.finish();
  r.push(" world");
  assert.equal(r.getText(), "hello", "finish 後不再累積");
});
