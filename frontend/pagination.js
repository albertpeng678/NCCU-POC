// pagination.js — 批次索引模型（支援前進/回上一批，pool 全保留）
export function createPaginationState(pool, batchSize){
  return { pool: Array.isArray(pool)?pool.slice():[], batchSize: batchSize>0?batchSize:10, batchIndex: -1 };
}
function slice(s){ const a=s.batchIndex*s.batchSize; return s.pool.slice(a, a+s.batchSize); }
export function nextBatch(s){
  if((s.batchIndex+1)*s.batchSize >= s.pool.length) return null;   // 無下一批（需續池）
  s.batchIndex += 1; return slice(s);
}
export function prevBatch(s){
  if(s.batchIndex <= 0) return null;                                // 已在第一批
  s.batchIndex -= 1; return slice(s);
}
export function batchPosition(s){
  const total = s.pool.length===0 ? 0 : Math.ceil(s.pool.length/s.batchSize);
  return { current: s.batchIndex+1, total, hasPrev: s.batchIndex>0, hasNext: (s.batchIndex+1)*s.batchSize < s.pool.length };
}
export function appendPool(state, more){
  const seen = new Set(state.pool.map(c=>c.course_id));
  for(const c of more||[]) if(!seen.has(c.course_id)){ seen.add(c.course_id); state.pool.push(c); }
  return state;
}
export function groupBatch(batch){            // ← 原樣保留（複製現有實作，勿改）
  const items=batch||[],n=items.length,g={core:[],supporting:[],extended:[]};
  if(n===0) return g;
  const coreEnd=Math.max(1,Math.ceil(n*0.4)), suppEnd=coreEnd+Math.ceil((n-coreEnd)*0.55);
  items.forEach((c,i)=>{const k=i<coreEnd?"core":i<suppEnd?"supporting":"extended";g[k].push(c);});
  return g;
}
