// pagination.js — 推薦結果 client-side 分頁純邏輯（無 DOM 依賴，可單測）。
// state: { pool: Course[], batchSize: number, shown: number, exhausted: boolean }

export function createPaginationState(pool, batchSize) {
  const state = {
    pool: Array.isArray(pool) ? pool.slice() : [],
    batchSize: batchSize > 0 ? batchSize : 10,
    shown: 0,
    exhausted: false,
  };
  return state;
}

// 取下一批（最多 batchSize 門）。回 null 表示池已抽乾（需續池）。
// 推進 state.shown；當 shown 已達 pool 長度時 exhausted=true。
export function nextBatch(state) {
  if (state.shown >= state.pool.length) {
    state.exhausted = true;
    return null;
  }
  const start = state.shown;
  const end = Math.min(start + state.batchSize, state.pool.length);
  const batch = state.pool.slice(start, end);
  state.shown = end;
  state.exhausted = state.shown >= state.pool.length;
  return batch;
}

// 續池 append：去重（依 course_id，已在 pool 者丟棄），接到 pool 尾。
// 不改 shown（已看過的不重看）；append 後 exhausted 視新長度更新。
export function appendPool(state, more) {
  const seen = new Set(state.pool.map((c) => c.course_id));
  for (const c of more || []) {
    if (!seen.has(c.course_id)) {
      seen.add(c.course_id);
      state.pool.push(c);
    }
  }
  state.exhausted = state.shown >= state.pool.length;
  return state;
}

// 把一批課依「批內推薦強度位置」分流成三區（確定性，保證三區依比例分配）：
// 前 ~40% = core（該批最推薦）、中 ~35% = supporting、其餘 = extended。
// 為何不用後端 course.group：fan-out 每技能只檢索「高度相關」課，池內幾乎全是相關課，
// 模型傾向全標 core（語意分類在此種池子難成立）。改以排名分桶 → 每批都有完整三區、
// 語意誠實（core=最推薦），且確定性可單測、不依賴 LLM。
export function groupBatch(batch) {
  const items = batch || [];
  const n = items.length;
  const g = { core: [], supporting: [], extended: [] };
  if (n === 0) return g;
  const coreEnd = Math.max(1, Math.ceil(n * 0.4));
  const suppEnd = coreEnd + Math.ceil((n - coreEnd) * 0.55);
  items.forEach((c, i) => {
    const key = i < coreEnd ? "core" : i < suppEnd ? "supporting" : "extended";
    g[key].push(c);
  });
  return g;
}
