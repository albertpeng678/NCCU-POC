// tests/frontend/pagination.test.mjs
// 前端分頁純邏輯單測（Node 內建 test runner，無外部依賴）。
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  createPaginationState,
  nextBatch,
  appendPool,
  groupBatch,
} from "../../frontend/pagination.js";

function pool(n, group = "core") {
  return Array.from({ length: n }, (_, i) => ({
    course_id: String(i).padStart(9, "0"),
    name: `課${i}`,
    group,
    rank: i,
  }));
}

test("E1: 30 課池 → 第 1 批為 rank 1-10", () => {
  const st = createPaginationState(pool(30), 10);
  const batch = nextBatch(st);
  assert.equal(batch.length, 10);
  assert.deepEqual(batch.map((c) => c.rank), [0,1,2,3,4,5,6,7,8,9]);
  assert.equal(st.shown, 10);
});

test("E2: 換一批 → 11-20、shown 推進", () => {
  const st = createPaginationState(pool(30), 10);
  nextBatch(st);
  const b2 = nextBatch(st);
  assert.deepEqual(b2.map((c) => c.rank), [10,11,12,13,14,15,16,17,18,19]);
  assert.equal(st.shown, 20);
});

test("E3: 再換 → 21-30、池乾", () => {
  const st = createPaginationState(pool(30), 10);
  nextBatch(st); nextBatch(st);
  const b3 = nextBatch(st);
  assert.deepEqual(b3.map((c) => c.rank), [20,21,22,23,24,25,26,27,28,29]);
  assert.equal(st.shown, 30);
  assert.equal(st.exhausted, true);
});

test("E4: 池乾再換 → 回 null（需續池訊號）、不崩", () => {
  const st = createPaginationState(pool(30), 10);
  nextBatch(st); nextBatch(st); nextBatch(st);
  const b4 = nextBatch(st);
  assert.equal(b4, null);
  assert.equal(st.exhausted, true);
});

test("E5: 池=7 (<batch) → 首批全 7、之後 exhausted", () => {
  const st = createPaginationState(pool(7), 10);
  const b1 = nextBatch(st);
  assert.equal(b1.length, 7);
  assert.equal(st.exhausted, true);
  assert.equal(nextBatch(st), null);
});

test("E6: groupBatch 依批內排名位置分三區（前40%core/中35%supporting/餘extended）", () => {
  // 10 門 → core 4（idx0-3）/ supporting 4（idx4-7）/ extended 2（idx8-9），與課程 group 欄位無關
  const batch = Array.from({ length: 10 }, (_, i) => ({
    course_id: `c${i}`, name: `第${i}`, group: "core", rank: i,
  }));
  const g = groupBatch(batch);
  assert.deepEqual(g.core.map((c) => c.name), ["第0", "第1", "第2", "第3"]);
  assert.deepEqual(g.supporting.map((c) => c.name), ["第4", "第5", "第6", "第7"]);
  assert.deepEqual(g.extended.map((c) => c.name), ["第8", "第9"]);
  assert.equal(g.core.length + g.supporting.length + g.extended.length, 10);
});

test("E6b: 全部同 group 的批次仍被分桶成三區（修『全core』bug）", () => {
  // 模擬後端把整池都標 core 的情況 → 前端仍應分出 supporting/extended
  const batch = Array.from({ length: 10 }, (_, i) => ({
    course_id: `c${i}`, name: `第${i}`, group: "core", rank: i,
  }));
  const g = groupBatch(batch);
  assert.ok(g.core.length > 0 && g.supporting.length > 0 && g.extended.length > 0,
    "三區都應有課，不可全擠在 core");
});

test("E7: 續池 append 去重 (course_id) 後再切批不重看", () => {
  const st = createPaginationState(pool(10), 10);
  nextBatch(st);                         // 看完 0-9
  // 續池：含 1 個重複 id (000000009) + 5 個新
  const more = [
    { course_id: "000000009", name: "課9dup", group: "core", rank: 0 },
    ...Array.from({ length: 5 }, (_, i) => ({
      course_id: String(100 + i).padStart(9, "0"),
      name: `新${i}`, group: "core", rank: i,
    })),
  ];
  appendPool(st, more);
  assert.equal(st.pool.length, 15);      // 10 + 5（重複的 9 被丟）
  const b2 = nextBatch(st);
  assert.deepEqual(b2.map((c) => c.name), ["新0","新1","新2","新3","新4"]);
});

test("groupBatch 缺某 group → 該區為空陣列", () => {
  const g = groupBatch([{ course_id: "a", name: "A", group: "core", rank: 0 }]);
  assert.deepEqual(g.supporting, []);
  assert.deepEqual(g.extended, []);
});
