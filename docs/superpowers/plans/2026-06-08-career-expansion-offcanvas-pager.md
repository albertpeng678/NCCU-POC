# Track B 實作計畫：職涯50→100 + offcanvas職類IA + 分頁回上一批

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development。前端任務**必須**照 playwright skill 流程用 Playwright MCP 做真 e2e + **桌機/平板/手機三裝置**驗收（使用者硬性要求），未過不得合併。

**Goal:** 職涯擴增至 100、offcanvas 改職能分類 accordion+搜尋、推薦分頁補「回上一批」。

**Architecture:** B-3 先做（純前端、`pagination.js` 改批次索引模型）；B-1 資料（`career_skills.json`/`careers.js`/`career_categories.json`）；B-2 offcanvas（用 B-1 分類資料）。

**Tech Stack:** 原生 JS、node:test（前端純邏輯）、pytest（後端資料一致性）、Playwright MCP（e2e+跨裝置）。分支 `feat/career-expansion`。

執行順序：Task 1→2（B-3）→ 3→4（B-1）→ 5（B-2）→ 6（收尾）。

---

## Task 1：pagination.js 改批次索引模型 + prevBatch（B-3 純邏輯）

**Files:** Modify `frontend/pagination.js`、`tests/frontend/pagination.test.mjs`

- [ ] **Step 1: 改寫測試（RED）** — 把 `tests/frontend/pagination.test.mjs` 的 `shown`/`exhausted` 斷言改為新模型，並新增 prevBatch/位置測試：

```javascript
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
```

- [ ] **Step 2: 跑測試確認失敗** — `node --test tests/frontend/pagination.test.mjs`，Expected: FAIL（`prevBatch`/`batchPosition` 未定義）。

- [ ] **Step 3: 改寫 `frontend/pagination.js`（保留 appendPool/groupBatch 不動）**：

```javascript
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
```

- [ ] **Step 4: 跑測試確認通過** — `node --test tests/frontend/pagination.test.mjs`，Expected: 全 PASS。
- [ ] **Step 5: Commit** — `git add frontend/pagination.js tests/frontend/pagination.test.mjs && git commit -m "feat(pager): pagination 改批次索引模型 + prevBatch/batchPosition（支援回上一批）" `（commit 結尾加 Co-Authored-By）。

---

## Task 2：B-3 導覽列 UI（index.html + app.js + style.css）

**Files:** Modify `frontend/index.html`（換掉 `#reroll-btn`）、`frontend/app.js`（reroll/prev 處理 + 渲染導覽列）、`frontend/style.css`（導覽列樣式，沿用 `mockup-pager.html` 變體 A）

- [ ] **Step 1: index.html** — 把單一 `<button id="reroll-btn">` 換成導覽列容器：
```html
<div id="pager" class="pager" hidden>
  <button id="prev-btn" class="pg-btn" type="button"><span class="arrow">‹</span> 上一批</button>
  <span id="pager-ind" class="pg-ind"></span>
  <button id="reroll-btn" class="pg-btn primary" type="button">換一批 <span class="arrow">›</span></button>
</div>
<div id="pager-dots" class="pg-dots" aria-hidden="true"></div>
```

- [ ] **Step 2: app.js** — 新增 `updatePager()` 並在 `renderBatch` 後呼叫；綁定 prev/換一批：
```javascript
import { createPaginationState, nextBatch, prevBatch, appendPool, groupBatch, batchPosition } from "./pagination.js?v=NN";
function updatePager(){
  const pager=document.getElementById("pager"), dots=document.getElementById("pager-dots");
  if(!pageState){ pager.hidden=true; dots.innerHTML=""; return; }
  const p=batchPosition(pageState); pager.hidden=false;
  document.getElementById("pager-ind").textContent = `第 ${p.current} / ${p.total} 批`;
  document.getElementById("prev-btn").classList.toggle("disabled", !p.hasPrev);
  document.getElementById("prev-btn").disabled = !p.hasPrev;
  dots.innerHTML = Array.from({length:p.total},(_,i)=>`<span class="dot${i===p.current-1?" on":""}"></span>`).join("");
}
// renderBatch(...) 之後都接 updatePager()（renderResults 首批、reroll、prev、續池收尾、rewatch）
document.getElementById("prev-btn").addEventListener("click", ()=>{
  if(!pageState) return; const b=prevBatch(pageState); if(b){ renderBatch(b); updatePager(); resultsEl.scrollIntoView({behavior:"smooth",block:"start"}); }
});
```
並把現有 `rerollBtn` handler 內 `renderBatch(batch)` 後加 `updatePager()`；rewatch 的 `pageState.shown=0;exhausted=false` 改為 `pageState.batchIndex=-1`，其後 `updatePager()`；end-state 收起導覽列改 `document.getElementById("pager").hidden=true`（取代原 `reroll-btn.style.display`）。

- [ ] **Step 3: style.css** — 從 `frontend/mockup-pager.html` 複製 `.pager/.pg-btn/.pg-btn.primary/.pg-btn.disabled/.pg-ind/.pg-dots/.dot/.arrow` 樣式（navy glass、無 emoji），併入既有 reroll 區樣式；手機 RWD：導覽列換行不擠。bump 所有資源 `?v=NN`。

- [ ] **Step 4: Playwright MCP 三裝置 e2e（GO 條件）** — 起本機後端（`QA_MODE=stream35 ALLOWED_ORIGIN=*`，port 8000），Playwright 載入 → 推薦一個職涯 → 驗：換一批→第2批、上一批→回第1批且資料一致、「第 N/M 批」與圓點正確、第1批上一批 disabled、翻到底 end-state、續池後 total 更新。**桌機(1280)/平板(820)/手機(390) 各跑一次、0 console error、截圖存證。**
- [ ] **Step 5: Commit**（`feat(pager): 導覽列 UI（上一批/第N/M批/換一批+圓點）+ 三裝置 e2e 驗收`）。

---

## Task 3：B-1 職涯資料（career_skills.json +50、careers.js 重生成、career_categories.json）

**Files:** Modify `backend/career_skills.json`、`frontend/careers.js`；Create `backend/career_categories.json`、`frontend/career-categories.js`；Test `tests/backend/test_career_data_integrity.py`

- [ ] **Step 1: 寫資料一致性測試（RED）** `tests/backend/test_career_data_integrity.py`：
```python
import json, re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def test_100_careers_and_categories_consistent():
    skills=json.loads((ROOT/"backend/career_skills.json").read_text(encoding="utf-8"))
    cats=json.loads((ROOT/"backend/career_categories.json").read_text(encoding="utf-8"))
    assert len(skills)==100, f"career_skills 應 100，實際 {len(skills)}"
    # 每職涯有非空 skills
    for k,v in skills.items(): assert v.get("skills"), f"{k} 缺 skills"
    # 分類涵蓋全部、無重複、無多餘
    flat=[c for arr in cats.values() for c in arr]
    assert len(flat)==len(set(flat)), "分類有重複職涯"
    assert set(flat)==set(skills.keys()), "分類與 career_skills 不一致（漏/多）"
    assert 8<=len(cats)<=10, "職能大類 8-10 個"
```
- [ ] **Step 2: 跑測試確認失敗**（`python -m pytest tests/backend/test_career_data_integrity.py -q`，FAIL：50≠100 / 無 career_categories.json）。
- [ ] **Step 3: 補資料**：
  - `backend/career_skills.json`：依 spec 8 分類的 50 新職涯，各補 `{label, skills:[6-8 中文技能 + 少量英文]}`（沿用現有 50 筆風格；技能要對得上政大課綱可檢索的詞）。
  - `backend/career_categories.json`：`{ "商業與管理":[...], "金融與財會":[...], ... }`，涵蓋全部 100、每職涯只歸一類（依 spec 分類）。
  - `frontend/careers.js`：`CAREERS` 重生成為 100 筆（與 career_skills key 一致）；`HOT_PICKS` 不變。
  - `frontend/career-categories.js`：`export` 或掛 `window.CAREER_CATEGORIES`（= career_categories.json 內容，供 offcanvas 渲染）。
- [ ] **Step 4: 跑測試確認通過** + 全套後端 `python -m pytest tests/backend -q` 綠。
- [ ] **Step 5: Commit**（`feat(careers): 職涯擴增至 100 + 8 職能分類資料`）。

---

## Task 4：B-1 後端離線補預算（career_budget）

**Files:** 用既有 `scripts/build_career_budget.py`（不改碼，跑增量）

- [ ] **Step 1: 增量灌新 50 職涯預算** — `ONLY_MISSING=1 CONCURRENCY=2 python scripts/build_career_budget.py`（對正式 DB；注意 embedding 限流分批）。Expected: 新職涯逐一寫入 `career_budget`，未中斷。
- [ ] **Step 2: 抽驗** — 隨機抽 3 個新職涯打 `POST /recommend` 確認命中預算秒回、結果合理。
- [ ] （無 commit；資料在 DB。記錄已灌數量於 HANDOFF。）

---

## Task 5：B-2 offcanvas 職類目錄重構（accordion + 搜尋）

**Files:** Modify `frontend/index.html`（`#offcanvas` 內容）、`frontend/app.js`（accordion 渲染/搜尋）、`frontend/style.css`（accordion/RWD）

- [ ] **Step 1: index.html** — `#offcanvas` 內容改為：標題 + 關閉鈕 + `#oc-search`（placeholder「搜尋職涯…」**無 emoji**）+ `#oc-cats` 容器（取代扁平 `#oc-list`，**移除熱門 chips**）。
- [ ] **Step 2: app.js** — 用 `CAREER_CATEGORIES` 渲染 accordion（沿用 `mockup-offcanvas.html` 結構）：每類一個可收合區塊（**預設全收合**）、點標題 toggle、點職涯走既有 `selectCareer`/`fetchRecommendation` 流程；`#oc-search` 輸入即時過濾：命中職涯跨類顯示、自動展開命中分類、高亮；清空恢復分類視圖。沿用既有 open/closeOffcanvas。
- [ ] **Step 3: style.css** — accordion 標題/數量標/展開動畫/item hover（navy glass、無 emoji，照 mockup）；手機抽屜全寬、overscroll-behavior:none 沿用。bump `?v=NN`。
- [ ] **Step 4: Playwright MCP 三裝置 e2e（GO 條件）** — 桌機/平板/手機各驗：開 offcanvas → 8 類顯示、展開/收合、點職涯觸發推薦、搜尋過濾+自動展開+高亮、手機全寬無 rubber-band、0 console error、截圖存證。
- [ ] **Step 5: Commit**（`feat(offcanvas): 職類分類 accordion + 搜尋（100 職涯可查找）+ 三裝置 e2e`）。

---

## Task 6：收尾

- [ ] **Step 1:** 全套測試綠 — `node --test tests/frontend/` + `python -m pytest tests/backend -q`。
- [ ] **Step 2:** superpowers:requesting-code-review 對整支 diff；Critical/Important 修掉。
- [ ] **Step 3:** **最終三裝置 e2e 整合**（B-1+B-2+B-3 一起走一遍真流程）。
- [ ] **Step 4:** 合併 master + push（Railway 部署）+ 線上煙霧。
- [ ] **Step 5:** 用 superpowers:finishing-a-development-branch 收尾，**刪 `feat/career-expansion` 分支**。

---

## Self-Review
- **Spec 覆蓋**：B-1(Task3/4)✓、B-2(Task5)✓、B-3(Task1/2)✓、Playwright 三裝置(Task2/5/6)✓、刪分支(Task6)✓。
- **Placeholder**：純邏輯(Task1)給完整 TDD 碼；UI/資料任務給關鍵碼 + 確切驗收指令；技能關鍵字內容於實作時生成（屬內容非邏輯）。
- **型別一致**：`createPaginationState/nextBatch/prevBatch/appendPool/groupBatch/batchPosition` 簽章 Task1 定義、Task2 同名呼叫；`CAREER_CATEGORIES` Task3 建、Task5 用；`career_categories.json` 結構一致。

**Out of scope**：推薦/Q&A 模型；File Search 成本優化。
