import { test } from "node:test";
import assert from "node:assert/strict";
import { createPaginationState, nextBatch, prevBatch, appendPool, groupBatch, batchPosition } from "../../frontend/pagination.js";
function pool(n){return Array.from({length:n},(_,i)=>({course_id:String(i).padStart(9,"0"),name:`課${i}`,group:"core",rank:i}));}

test("E1: 首批 rank 0-9、位置 1/3", ()=>{const st=createPaginationState(pool(30),10);const b=nextBatch(st);
  assert.deepEqual(b.map(c=>c.rank),[0,1,2,3,4,5,6,7,8,9]);assert.deepEqual(batchPosition(st),{current:1,total:3,hasPrev:false,hasNext:true});});
test("E2: 換一批 → 10-19、位置 2/3、可回上一批", ()=>{const st=createPaginationState(pool(30),10);nextBatch(st);const b=nextBatch(st);
  assert.deepEqual(b.map(c=>c.rank),[10,11,12,13,14,15,16,17,18,19]);assert.deepEqual(batchPosition(st),{current:2,total:3,hasPrev:true,hasNext:true});});
test("E3: 第3批 → 20-29、hasNext=false", ()=>{const st=createPaginationState(pool(30),10);nextBatch(st);nextBatch(st);const b=nextBatch(st);
  assert.deepEqual(b.map(c=>c.rank),[20,21,22,23,24,25,26,27,28,29]);assert.equal(batchPosition(st).hasNext,false);});
test("E4: 池乾再 nextBatch → null、batchIndex 不動", ()=>{const st=createPaginationState(pool(30),10);nextBatch(st);nextBatch(st);nextBatch(st);
  assert.equal(nextBatch(st),null);assert.equal(batchPosition(st).current,3);});
test("P1: prevBatch 回上一批 → rank 0-9、位置回 1/3", ()=>{const st=createPaginationState(pool(30),10);nextBatch(st);nextBatch(st);const b=prevBatch(st);
  assert.deepEqual(b.map(c=>c.rank),[0,1,2,3,4,5,6,7,8,9]);assert.deepEqual(batchPosition(st),{current:1,total:3,hasPrev:false,hasNext:true});});
test("P2: 第1批 prevBatch → null、不動", ()=>{const st=createPaginationState(pool(30),10);nextBatch(st);
  assert.equal(prevBatch(st),null);assert.equal(batchPosition(st).current,1);});
test("E5: 池=7 (<batch) → 首批全7、hasNext=false、total 1", ()=>{const st=createPaginationState(pool(7),10);const b=nextBatch(st);
  assert.equal(b.length,7);assert.deepEqual(batchPosition(st),{current:1,total:1,hasPrev:false,hasNext:false});});
test("E7: 續池 append 去重後 hasNext 恢復、不重看", ()=>{const st=createPaginationState(pool(10),10);nextBatch(st);
  appendPool(st,[{course_id:"000000009",name:"dup",group:"core",rank:0},...Array.from({length:5},(_,i)=>({course_id:String(100+i).padStart(9,"0"),name:`新${i}`,group:"core",rank:i}))]);
  assert.equal(st.pool.length,15);assert.equal(batchPosition(st).hasNext,true);
  assert.deepEqual(nextBatch(st).map(c=>c.name),["新0","新1","新2","新3","新4"]);});
test("E6: groupBatch 依批內位置分三區", ()=>{const batch=Array.from({length:10},(_,i)=>({course_id:`c${i}`,name:`第${i}`,group:"core",rank:i}));
  const g=groupBatch(batch);assert.deepEqual(g.core.map(c=>c.name),["第0","第1","第2","第3"]);
  assert.deepEqual(g.supporting.map(c=>c.name),["第4","第5","第6","第7"]);assert.deepEqual(g.extended.map(c=>c.name),["第8","第9"]);});
test("P3: 全新狀態(未 nextBatch) prevBatch → null", ()=>{
  const st=createPaginationState(pool(30),10);
  assert.equal(prevBatch(st), null);
});
test("B1: 初始狀態 batchPosition = {current:0,total:3,hasPrev:false,hasNext:true}", ()=>{
  const st=createPaginationState(pool(30),10);
  assert.deepEqual(batchPosition(st), {current:0,total:3,hasPrev:false,hasNext:true});
});
