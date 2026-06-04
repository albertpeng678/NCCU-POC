# 串流即時進度 UX — 前端實作計畫（SSE）

> 日期：2026-06-04（Session 2）
> 對應設計文件：`docs/superpowers/specs/2026-06-04-streaming-progress-ux-design.md`
> 範圍：**僅前端**（`frontend/app.js` / `frontend/style.css` / `frontend/index.html`）。後端 `GET /recommend/stream`、`GET /qa/stream` 由後端 agent 依同一份 SSE 契約實作。

**REQUIRED SUB-SKILL: superpowers:subagent-driven-development**

---

## Goal

把兩個模式的「黑箱等待」升級成 SSE 即時體驗：

1. **職涯推薦**：用 `EventSource(GET /recommend/stream)` 接 `stage`/`result`/`no_match`/`error` 事件，驅動 **5 階段垂直 stepper + cyan 漸層進度條**（done 打勾轉綠 / active 脈動+spinner / pending 灰），底部顯示「第 X/5 步 · 預估還需約 N 秒」。階段內用 easing 平滑推到該段配額 ~90% 後 hold 等真事件（超時安全網）。SSE 連線失敗或首事件逾時 → fallback 改打舊 `POST /recommend` + 退回校準模擬 5 階段動畫。
2. **自由問答**：用 `EventSource(GET /qa/stream)` 接 `token`/`done`/`error` 事件，**緩衝 token + 固定節奏吐字（~34 cps，與到達速度脫鉤）**，cyan 閃爍游標，markdown 安全逐區塊渲染（節流 ~80ms，只渲染到最後一個完整區塊邊界、未閉合 inline 先藏、表格整塊淡入），打完才淡入 citations + followup chips。SSE 失敗 → fallback 打舊 `POST /qa` 拿完整答案後 client 端逐字播放。

**核心原則：誠實。** 進度永不謊報完成；超時只 hold + 換文案。

## Architecture

- 既有 `frontend/app.js` 是單檔原生 JS，無模組系統、無建置步驟。新增的串流邏輯**直接追加到同一檔**，沿用既有 `CONFIG`、`escHtml`、`renderResults`、`renderCard`、`showNoMatch`、`appendUserBubble`、`appendBotBubble`、`renderAnswerHtml`、`setMode` 等函式。
- 既有推薦載入 UI 是「2 步驟 + 70s 進度條」（`showLoading`/`stopLoading`/`LOAD_TIPS`/`_loadTimers`，app.js 行 223-249）。本計畫**改寫 loading 區 DOM（index.html 行 82-86）成 5 階段 stepper**，並把 `showLoading` 改成「校準模擬」版（fallback 用），新增 `startStreamRecommend`（SSE 版）作為主路徑。
- 既有問答用 `fetch POST /qa` + `appendLoadingBubble`/`appendBotBubble`（app.js 行 406-449）。本計畫新增 `startStreamQa`（SSE 主路徑，含打字機引擎），保留 `askQuestionFallback`（原 `askQuestion` 改名，POST 版）。`askQuestion` 改為「先試串流、失敗降級」的 dispatcher。
- SSE 端點 URL 由 `CONFIG.API_URL` 衍生：`${CONFIG.API_URL}/recommend/stream?...`、`${CONFIG.API_URL}/qa/stream?...`。
- `EventSource` 預設帶 cookie/credentials 為 same-origin；跨網域 SSE 需後端 CORS 放行（後端範圍，前端不需設定）。

## Tech Stack

- 原生 HTML / CSS / JS（無框架、無建置）。
- `EventSource`（瀏覽器原生 SSE）。
- `marked@15.0.7` + `DOMPurify@3.2.4`（既有 CDN，用於 Q&A markdown 安全渲染）。
- `@sentry/browser`（既有 CDN，串流錯誤需 `Sentry.captureException`）。
- 驗證：**Playwright E2E（無 JS 單元測試框架，CLAUDE.md 慣例）**。整合 E2E 在後續 Wave 統一跑；本計畫每個 Task 列出截圖 viewport（手機 375 / 平板 768 / 桌機 1280）與預期視覺/行為驗證點。

## 共用契約（所有 Task 共用，務必一致）

SSE 事件（後端照同一份）：

推薦 `GET /recommend/stream?career=<urlencoded>&seed=<int?>`：
```
event: stage     data: {"n":1,"key":"understand","status":"start"|"done"}
event: stage     data: {"n":2,"key":"retrieve","status":...}
event: stage     data: {"n":3,"key":"filter","status":...}
event: stage     data: {"n":4,"key":"compose","status":...}
event: stage     data: {"n":5,"key":"finalize","status":...}
event: result    data: {<完整 RecommendResponse JSON>}
event: no_match  data: {"career":"...","message":"..."}
event: error     data: {"error_type":"...","message":"..."}
: keep-alive
```
推薦 5 階段（key 依序、固定）：
```
STAGES = [
  {key:"understand", label:"理解你的職涯方向"},
  {key:"retrieve",   label:"檢索全校課綱"},
  {key:"filter",     label:"篩選候選課程"},
  {key:"compose",    label:"編排推薦組合與理由"},
  {key:"finalize",   label:"整理課程資訊"},
]
```

問答 `GET /qa/stream?question=<urlencoded>&session_id=<id?>`：
```
event: token     data: {"text":"…部分文字…"}
event: done      data: {"citations":[...],"followup_suggestions":[...],"session_id":"...","turn_number":N}
event: error     data: {"error_type":"...","message":"..."}
: keep-alive
```
> 注意：`done` 的 payload 欄位命名與既有 `appendBotBubble`（讀 `data.citations` / `data.followup_suggestions` / `data.session_id` / `data.turn_number`）一致，方便重用渲染函式。後端須照此命名。

---

## Task 1：index.html — 改寫推薦 loading 為 5 階段 stepper + 新增 Q&A 串流容器 + bump 版本

**Files**
- Modify `C:\side\NCCU-poc\frontend\index.html`（loading 區 行 71-88；資源連結 行 10、155-156）

### Step 1.1 — 改寫 loading section 成 5 階段 stepper（行 71-88）

把 index.html 行 71-88 的整段 `<section id="loading">` 換成下列（保留 `id="loading"`、`load-card`、`load-bar`/`load-bar-fill`，改 steps 為 5 個含勾選 SVG，新增 active spinner、底部步數列）：

```html
      <!-- Loading（5 階段串流進度；SSE 事件驅動，fallback 用校準模擬） -->
      <section id="loading" class="loading" hidden aria-live="polite">
        <div class="load-card">
          <div class="load-head">
            <span class="loader-ring" aria-hidden="true"></span>
            <div class="load-head-txt">
              <p class="load-title">AI 正在為你規劃課程組合</p>
              <p class="load-eta" id="load-eta">第 <b id="load-step-num">1</b>/5 步 · 預估還需約 <b id="load-eta-sec">50</b> 秒</p>
            </div>
          </div>
          <div class="load-bar" aria-hidden="true"><span id="load-bar-fill" class="load-bar-fill"></span></div>
          <ol class="load-steps" id="load-steps">
            <li class="ld-step" data-key="understand"><span class="ld-mark" aria-hidden="true"></span>理解你的職涯方向</li>
            <li class="ld-step" data-key="retrieve"><span class="ld-mark" aria-hidden="true"></span>檢索全校課綱</li>
            <li class="ld-step" data-key="filter"><span class="ld-mark" aria-hidden="true"></span>篩選候選課程</li>
            <li class="ld-step" data-key="compose"><span class="ld-mark" aria-hidden="true"></span>編排推薦組合與理由</li>
            <li class="ld-step" data-key="finalize"><span class="ld-mark" aria-hidden="true"></span>整理課程資訊</li>
          </ol>
          <p class="load-tip" id="load-tip"></p>
        </div>
      </section>
```

### Step 1.2 — bump 資源版本號（cache-bust）

- 行 10：`<link rel="stylesheet" href="style.css?v=2">` → `href="style.css?v=3"`
- 行 155：`<script src="careers.js?v=2"></script>` → `careers.js?v=3`
- 行 156：`<script src="app.js?v=2"></script>` → `app.js?v=3`

### Step 1.3 — 驗證

- Playwright：`browser_navigate` 到本機 frontend，於 console 執行 `document.getElementById('load-steps').children.length` 應為 5；每個 `.ld-step` 有 `data-key`。
- 截圖 viewport 1280：頁面正常載入、無 console 錯誤（此時 stepper 仍 hidden，僅確認結構與 CSS 引用 v=3 生效）。
- 不需 commit（與後續 Task 同 commit；見 Task 6）。

---

## Task 2：style.css — 5 階段 stepper 樣式（done/active/pending + spinner + 進度條轉綠）

**Files**
- Modify `C:\side\NCCU-poc\frontend\style.css`（取代既有 `.load-steps` 區塊 行 314-323；新增於該區附近）

### Step 2.1 — 取代既有 `.load-steps` 樣式（行 314-323）

把 style.css 行 314-323（`.load-steps` 到 `@keyframes ldpulse` 到 `.load-tip` 前）整段，換成下列 5 階段 stepper 樣式。注意 `.load-tip`（行 322）與 `.reroll-ic`（行 323）**保留不動**，只替換 `.load-steps` 相關到 `@keyframes ldpulse`：

```css
/* ===== 5 階段 stepper（SSE 進度）===== */
.load-steps{list-style:none;display:flex;flex-direction:column;gap:13px;margin:0}
.ld-step{display:flex;align-items:center;gap:11px;font-size:13.5px;color:var(--text-dim);transition:color .3s}
.ld-step .ld-mark{
  width:20px;height:20px;flex-shrink:0;border-radius:50%;
  border:2px solid var(--glass-brd);background:transparent;
  display:grid;place-items:center;position:relative;transition:all .3s;
}
/* pending：灰圈 */
.ld-step .ld-mark::after{content:"";width:6px;height:6px;border-radius:50%;background:var(--glass-brd);transition:all .3s}
/* active：cyan 脈動 + 旋轉 spinner（蓋掉內點）*/
.ld-step.active{color:var(--text)}
.ld-step.active .ld-mark{border-color:var(--cyan);border-top-color:transparent;animation:ldspin .8s linear infinite,ldpulse 1.4s ease-in-out infinite}
.ld-step.active .ld-mark::after{opacity:0}
/* done：綠勾 */
.ld-step.done{color:rgba(232,240,255,.6)}
.ld-step.done .ld-mark{border-color:#37e0a6;background:#37e0a6;animation:none}
.ld-step.done .ld-mark::after{content:"";width:9px;height:5px;border:2px solid var(--navy-void);border-top:none;border-right:none;border-radius:0;background:transparent;transform:translateY(-1px) rotate(-45deg);opacity:1}
/* error：紅圈叉 */
.ld-step.error{color:#ff9b9b}
.ld-step.error .ld-mark{border-color:#ff8181;background:transparent;animation:none}
.ld-step.error .ld-mark::after{content:"×";width:auto;height:auto;color:#ff8181;font-size:14px;line-height:1;background:transparent}
@keyframes ldspin{to{transform:rotate(360deg)}}
@keyframes ldpulse{0%,100%{box-shadow:0 0 0 2px var(--cyan-dim)}50%{box-shadow:0 0 0 6px transparent}}
```

### Step 2.2 — 進度條完成轉綠 + 步數列強調樣式

在 Step 2.1 區塊後（`.load-tip` 行之前或之後皆可，建議緊接 `.load-bar-fill` 定義之後）新增。先在既有 `.load-bar-fill`（行 313）後新增 `.load-bar-fill.done` 與 `.load-eta` 修飾：

在 style.css 行 313（`.load-bar-fill{...}`）之後新增一行樣式區：

```css
.load-bar-fill{transition:width .6s cubic-bezier(.25,.8,.3,1)}
.load-bar-fill.done{background:linear-gradient(90deg,#37e0a6,#7af0c8)}
.load-eta b{color:var(--cyan);font-weight:600}
.load-card.done .load-title{color:#37e0a6}
.load-card.done .loader-ring{border-top-color:#37e0a6;animation:none;opacity:.4}
```

> 注意：既有行 311 已有 `.load-eta b{color:var(--cyan)...}`；上面重複宣告無害（後者覆寫相同值），但為避免冗餘，實作時若行 311 仍存在可省略此處的 `.load-eta b`。`.load-bar-fill` 的 `transition` 在此設定預設值，app.js 會在模擬模式時用 inline style 覆寫成 easing 長時程。

### Step 2.3 — RWD：小螢幕 stepper 不破版

在 style.css 既有 `@media (max-width:768px)` 區塊（行 283-286，Q&A composer 那段）**之外**，於 5 階段樣式區後新增專屬 media query：

```css
@media (max-width:480px){
  .load-card{padding:20px 18px;width:min(520px,94vw)}
  .ld-step{font-size:12.5px;gap:9px}
  .ld-step .ld-mark{width:18px;height:18px}
  .load-title{font-size:15px}
  .load-eta{font-size:12px}
}
```

### Step 2.4 — 驗證

- Playwright + console：手動加 class 驗證三態。`browser_navigate` 後在 console 執行：
  ```js
  loadingEl.hidden=false;
  document.querySelectorAll('.ld-step')[0].classList.add('done');
  document.querySelectorAll('.ld-step')[1].classList.add('active');
  ```
- 截圖 viewport 1280 / 768 / 375：第 1 步綠勾、第 2 步 cyan 旋轉脈動、第 3-5 步灰圈；卡片不溢出、文字不換行爆版。
- 不需 commit（見 Task 6）。

---

## Task 3：app.js — 推薦 SSE 主路徑（5 階段事件 + easing 90%-hold + es.close + fallback）

**Files**
- Modify `C:\side\NCCU-poc\frontend\app.js`（改寫 loading 區 行 222-249；改寫 `fetchRecommendation` 行 252-272）

### Step 3.1 — 新增推薦階段常數與進度狀態（放在 app.js 行 221 `// ---------- States ----------` 之後）

在 app.js 既有 `const LOAD_TIPS = [...]`（行 223-228）**上方**插入 5 階段定義與進度狀態：

```js
// ---------- 推薦 5 階段（SSE 串流）----------
const REC_STAGES = [
  {key:"understand", label:"理解你的職涯方向"},
  {key:"retrieve",   label:"檢索全校課綱"},
  {key:"filter",     label:"篩選候選課程"},
  {key:"compose",    label:"編排推薦組合與理由"},
  {key:"finalize",   label:"整理課程資訊"},
];
// 每階段在進度條佔的「累積上限 %」（誠實：done 才補到該段終點，active 期間 easing 推到該段 90%）
const STAGE_END = {understand:12, retrieve:55, filter:63, compose:90, finalize:100};
// 各階段預估剩餘秒（用於「預估還需約 N 秒」遞減估算）
const STAGE_SEC = {understand:4, retrieve:24, filter:2, compose:18, finalize:2};
let _recEs = null;          // 當前 EventSource
let _stageRaf = null;       // easing 動畫 rAF handle
let _curStageIdx = -1;      // 目前 active 階段 index
```

### Step 3.2 — 改寫 stepper 控制函式（取代既有 `stopLoading`/`showLoading` 行 229-249）

把 app.js 行 229-249（`let _loadTimers` 到 `showLoading` 結尾 `}`）整段替換成下列。新增：`resetStepper`、`setStageActive`、`setStageDone`、`setStageError`、`easeWithinStage`、`finishProgress`、`updateEta`、保留 `stopLoading` 與 `LOAD_TIPS` 輪播，並把舊 `showLoading`（2 步驟模擬）改名為 `showLoadingSimulated`（fallback 專用、走 5 階段時間表）：

```js
let _loadTimers = [];
function stopLoading(){
  _loadTimers.forEach(t=>{clearInterval(t);clearTimeout(t);}); _loadTimers = [];
  if(_stageRaf){ cancelAnimationFrame(_stageRaf); _stageRaf = null; }
}

// --- DOM refs（loading 區）---
function _loadRefs(){
  return {
    card: document.querySelector("#loading .load-card"),
    bar:  document.getElementById("load-bar-fill"),
    steps:Array.from(document.querySelectorAll("#load-steps .ld-step")),
    tip:  document.getElementById("load-tip"),
    stepNum: document.getElementById("load-step-num"),
    etaSec:  document.getElementById("load-eta-sec"),
  };
}

function resetStepper(){
  const r = _loadRefs();
  if(!r.bar || !r.steps.length) return;
  _curStageIdx = -1;
  r.card && r.card.classList.remove("done");
  r.bar.classList.remove("done");
  r.bar.style.transition = "none";
  r.bar.style.width = "0%";
  r.steps.forEach(s=>s.classList.remove("active","done","error"));
  if(r.stepNum) r.stepNum.textContent = "1";
  // 立即啟動第一階段
  setStageActive(0);
}

// 進度條目標（％）平滑過渡到 target；用 CSS transition（Step 2.2 預設 .6s）
function moveBar(targetPct, durationMs){
  const r = _loadRefs(); if(!r.bar) return;
  r.bar.style.transition = `width ${durationMs}ms cubic-bezier(.25,.8,.3,1)`;
  requestAnimationFrame(()=>{ r.bar.style.width = `${targetPct}%`; });
}

function updateEta(){
  const r = _loadRefs(); if(!r.etaSec) return;
  // 從目前階段起，加總剩餘各階段預估秒
  let sec = 0;
  for(let i=Math.max(_curStageIdx,0); i<REC_STAGES.length; i++){
    sec += STAGE_SEC[REC_STAGES[i].key] || 0;
  }
  r.etaSec.textContent = String(Math.max(sec,1));
  if(r.stepNum) r.stepNum.textContent = String(Math.min(_curStageIdx+1, 5));
}

// active：標記脈動 + easing 把進度條推到「該段終點的 90%」就 hold（誠實安全網）
function setStageActive(idx){
  const r = _loadRefs(); if(!r.steps.length) return;
  _curStageIdx = idx;
  r.steps.forEach((s,i)=>{
    if(i < idx) { s.classList.add("done"); s.classList.remove("active","error"); }
    else if(i === idx){ s.classList.add("active"); s.classList.remove("done","error"); }
    else { s.classList.remove("active","done","error"); }
  });
  const key = REC_STAGES[idx].key;
  const prevEnd = idx === 0 ? 0 : STAGE_END[REC_STAGES[idx-1].key];
  const thisEnd = STAGE_END[key];
  const hold = prevEnd + (thisEnd - prevEnd) * 0.9;   // 推到該段 90%
  // 用該階段預估秒 easing 推進；到 hold 就停（CSS transition 自然 hold）
  moveBar(hold, (STAGE_SEC[key] || 4) * 1000);
  updateEta();
}

// done：補滿該段終點、勾選；不自動跳下一段（等下一個 stage start 事件）
function setStageDone(idx){
  const r = _loadRefs(); if(!r.steps.length) return;
  const step = r.steps[idx]; if(!step) return;
  step.classList.add("done"); step.classList.remove("active","error");
  const key = REC_STAGES[idx].key;
  moveBar(STAGE_END[key], 500);   // 補滿該段終點，給「躍進」感
}

function setStageError(idx, msg){
  const r = _loadRefs();
  const step = r.steps[idx >= 0 ? idx : 0];
  if(step){ step.classList.add("error"); step.classList.remove("active"); }
  stopLoading();
}

// 全部完成：補滿 100% + 轉綠
function finishProgress(){
  const r = _loadRefs();
  r.steps.forEach(s=>{ s.classList.add("done"); s.classList.remove("active","error"); });
  r.bar && r.bar.classList.add("done");
  r.card && r.card.classList.add("done");
  moveBar(100, 400);
  if(r.stepNum) r.stepNum.textContent = "5";
  if(r.etaSec) r.etaSec.textContent = "0";
}

// 輪播提示文案（沿用，降低等待煎熬）
const LOAD_TIPS = [
  "正在比對你的職涯所需技能與課程內容…",
  "從全校 2,700+ 門課綱中逐一篩選相關課程…",
  "為每門課量身生成推薦理由，請再稍候…",
  "好課值得等待，馬上就好。",
];
function startTips(){
  const r = _loadRefs(); if(!r.tip) return;
  let i=0; r.tip.textContent = LOAD_TIPS[0];
  _loadTimers.push(setInterval(()=>{ i=(i+1)%LOAD_TIPS.length; r.tip.textContent = LOAD_TIPS[i]; }, 5000));
}

// 顯示 loading 區（共用：SSE 與 fallback 都先呼叫）
function showLoadingShell(){
  resultsEl.hidden = true; errorEl.hidden = true;
  const nm = document.getElementById("no-match"); if(nm) nm.hidden = true;
  loadingEl.hidden = false;
  loadingEl.scrollIntoView({behavior:"smooth",block:"center"});
  stopLoading();
  resetStepper();
  startTips();
}

// fallback：校準模擬 5 階段（SSE 失敗時用；依 STAGE_SEC 時間表自動推進）
function showLoadingSimulated(){
  showLoadingShell();
  let acc = 0;
  // 依序在估計時間點把前一階段標 done、下一階段 active
  for(let i=1; i<REC_STAGES.length; i++){
    acc += (STAGE_SEC[REC_STAGES[i-1].key] || 4) * 1000;
    const idx = i;
    _loadTimers.push(setTimeout(()=>{ setStageDone(idx-1); setStageActive(idx); }, acc));
  }
}
```

### Step 3.3 — 改寫 `fetchRecommendation`（取代行 252-272）

把 app.js 行 252-272（`// ---------- API ----------` 與 `async function fetchRecommendation`）整段替換成：SSE 主路徑 `startStreamRecommend` + fallback `postRecommend`，`fetchRecommendation` 作為 dispatcher。

```js
// ---------- API：推薦 ----------
function closeRecEs(){ if(_recEs){ try{_recEs.close();}catch(_){} _recEs = null; } }

function fetchRecommendation(career){
  lastCareer = career;            // 供「換一批」沿用（含清單外職涯）
  showLoadingShell();
  startStreamRecommend(career);
}

// SSE 主路徑
function startStreamRecommend(career){
  closeRecEs();
  const seed = Math.floor(Math.random() * 1e9);   // 換一批多樣性
  let firstEvent = false;
  let settled = false;            // 已收 result/no_match/error
  const url = `${CONFIG.API_URL}/recommend/stream?career=${encodeURIComponent(career)}&seed=${seed}`;

  // 首事件逾時安全網：8s 內無任何事件 → 視為 proxy 擋串流，降級
  const guard = setTimeout(()=>{
    if(!firstEvent && !settled){
      closeRecEs();
      postRecommend(career);      // fallback
    }
  }, 8000);
  _loadTimers.push(guard);

  let es;
  try{ es = new EventSource(url); }
  catch(e){ clearTimeout(guard); postRecommend(career); return; }
  _recEs = es;

  es.addEventListener("stage", (ev)=>{
    firstEvent = true;
    let d; try{ d = JSON.parse(ev.data); }catch(_){ return; }
    const idx = (d.n|0) - 1;
    if(idx < 0 || idx >= REC_STAGES.length) return;
    if(d.status === "start") setStageActive(idx);
    else if(d.status === "done") setStageDone(idx);
  });

  es.addEventListener("result", (ev)=>{
    settled = true; clearTimeout(guard); closeRecEs();
    let data; try{ data = JSON.parse(ev.data); }catch(e){ showError("回傳資料解析失敗，請稍後再試。"); return; }
    finishProgress();
    setTimeout(()=>{
      if(data && data.no_match){ showNoMatch(data.career || lastCareer, data.message || "目前沒有找到相關課程。"); }
      else renderResults(data);
    }, 350);   // 讓「補滿+轉綠」被看到再切結果
  });

  es.addEventListener("no_match", (ev)=>{
    settled = true; clearTimeout(guard); closeRecEs();
    let d; try{ d = JSON.parse(ev.data); }catch(_){ d = {}; }
    stopLoading();
    showNoMatch(d.career || lastCareer, d.message || "目前沒有找到相關課程。");
  });

  es.addEventListener("error_event", ()=>{});   // 佔位避免誤判（實際錯誤用 "error" 自訂事件）
  es.addEventListener("error", (ev)=>{
    // 區分：自訂 error 事件（有 data）vs EventSource 連線層 error（無 data）
    if(ev && typeof ev.data === "string" && ev.data.length){
      settled = true; clearTimeout(guard); closeRecEs();
      let d; try{ d = JSON.parse(ev.data); }catch(_){ d = {}; }
      setStageError(_curStageIdx, d.message);
      if(window.Sentry) Sentry.captureMessage(`recommend stream error: ${d.error_type||"unknown"}`);
      showError(`查詢失敗：${d.message || "服務暫時繁忙"}。請稍後再試。`);
      return;
    }
    // 連線層 error：若尚未收任何事件且未 settled → 降級；已串流中途斷 → 報錯
    if(settled) return;
    clearTimeout(guard); closeRecEs();
    if(!firstEvent){ postRecommend(career); }      // proxy 擋串流 → fallback
    else { showError("連線中斷，請稍後再試。"); }
  });
}

// fallback：舊 POST /recommend + 校準模擬動畫
async function postRecommend(career){
  showLoadingSimulated();
  try{
    const resp = await fetch(`${CONFIG.API_URL}/recommend`,{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({career}),
    });
    if(!resp.ok){
      const err = await resp.json().catch(()=>({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    const data = await resp.json();
    finishProgress();
    setTimeout(()=>{
      if(data && data.no_match){ showNoMatch(career, data.message || "目前沒有找到相關課程。"); }
      else renderResults(data);
    }, 350);
  }catch(e){
    if(window.Sentry) Sentry.captureException(e);
    showError(`查詢失敗：${e.message}。請稍後再試。`);
  }
}
```

> 注意：`renderResults`（行 191）開頭呼叫 `stopLoading()`，會清掉 `_stageRaf`/timers，與本流程相容。`showError`（行 250）亦呼叫 `stopLoading()`。`closeRecEs` 已先關閉 SSE，無重連風險。

### Step 3.4 — 驗證

- Playwright E2E（真後端，需後端 `/recommend/stream` 已上線；若後端未就緒，先以下方 console 模擬驗證 UI）：
  - 桌機 1280：選清單內職涯送出 → 5 階段依序亮（done 綠勾 / active 旋轉）、進度條躍進、底部「第 X/5 步」遞增、最後補滿轉綠再出結果。截圖各階段。
  - 清單外職涯：階段 1 停較久（真 AI）。
  - no_match：出現溫和卡片、進度優雅停止不假完成。
- UI 模擬（後端未就緒時）：console 執行 `showLoadingShell(); setStageDone(0); setStageActive(1);` 觀察轉場；`finishProgress()` 觀察轉綠。
- fallback 模擬：console 暫時把 `CONFIG.API_URL` 指到不存在埠或 `startStreamRecommend` 內 throw，確認 8s guard 後走 `postRecommend` + 模擬動畫。
- 截圖 viewport 375 / 768 / 1280。
- 不需 commit（見 Task 6）。

---

## Task 4：app.js — 問答打字機引擎（固定節奏吐字 + markdown 安全逐區塊渲染）

**Files**
- Modify `C:\side\NCCU-poc\frontend\app.js`（在 Q&A 區 `renderAnswerHtml` 行 352-366 附近新增打字機引擎；不改既有函式本體）

### Step 4.1 — 新增「安全渲染部分 markdown」工具（放在 `renderAnswerHtml` 行 366 之後）

新增 `renderPartialMarkdown`：累積 raw 文字 → 切到「最後一個完整區塊邊界」→ 未閉合 inline `**` 先藏 → marked + DOMPurify。回傳 `{html, hiddenTail}`，hiddenTail 為被藏起來的尾段（下次補渲染）。

```js
// 從累積 raw markdown 取「可安全渲染的前綴」：
// 1) 表格未收尾（最後一段是 | 開頭但後面沒有空行）整塊先藏，避免半截爆版
// 2) 尾端未閉合的 ** 先去掉（避免粗體吃掉後文）
function safeMarkdownPrefix(raw){
  let text = raw;
  // (a) 若最後一個區塊是「進行中的表格」（含 | 但尾端非空行結束）→ 砍到該表格前
  const lines = text.split("\n");
  // 找最後一個空行，作為「最後一個完整區塊」的安全切點候選
  // 規則：若文字未以雙換行結尾，且尾段包含表格列（以 | 起頭），把尾段整塊延後
  let cut = text.length;
  const tailStart = text.lastIndexOf("\n\n");
  const tail = tailStart >= 0 ? text.slice(tailStart+2) : text;
  const tailIsTable = /^\s*\|/.test(tail) || /\n\s*\|/.test(tail);
  const endsClean = /\n\s*$/.test(text);
  if(tailIsTable && !endsClean){
    cut = tailStart >= 0 ? tailStart : 0;   // 整塊表格延後到收尾
  }
  let prefix = text.slice(0, cut);
  // (b) 未閉合的 ** ：count 為奇數則砍掉最後一個 ** 之後
  const boldMatches = prefix.match(/\*\*/g);
  if(boldMatches && boldMatches.length % 2 === 1){
    const last = prefix.lastIndexOf("**");
    prefix = prefix.slice(0, last);
  }
  return prefix;
}

function renderSafeMarkdown(raw){
  const prefix = safeMarkdownPrefix(raw);
  if (typeof marked === "undefined" || typeof DOMPurify === "undefined"){
    return `<div class="ans">${escHtml(prefix).replace(/\n/g,"<br>")}</div>`;
  }
  const html = marked.parse(prefix, {breaks:true, gfm:true});
  const safe = DOMPurify.sanitize(html, {
    ALLOWED_TAGS: ["p","br","strong","em","u","h1","h2","h3","h4","ul","ol","li",
                   "table","thead","tbody","tr","th","td","blockquote","code","pre","a","hr"],
    ALLOWED_ATTR: ["href","target","rel"],
  });
  return safe;   // 不含外層 .ans，由打字機容器負責
}
```

### Step 4.2 — 新增打字機引擎（接 Step 4.1 之後）

`Typewriter`：緩衝 token、固定 ~34 cps 節奏吐字、節流 80ms 重渲染安全 markdown、cyan 游標、完成 callback。

```js
// 固定節奏打字機：token 進緩衝，定時吐字（與到達速度脫鉤）
const TYPE_CPS = 34;                 // 中速 34 字/秒（使用者選定）
const TYPE_INTERVAL = 1000 / TYPE_CPS;
const RENDER_THROTTLE = 80;          // markdown 重渲染節流

function createTypewriter(ansEl){
  let buffer = "";        // 尚未吐出的 token 文字
  let shown = "";         // 已吐出的 raw markdown
  let inputDone = false;  // 後端 token 是否已全部到齊（done 事件）
  let onComplete = null;
  let typeTimer = null;
  let renderTimer = null;
  let lastRender = 0;

  function scheduleRender(){
    const now = performance.now();
    if(now - lastRender >= RENDER_THROTTLE){
      doRender(); 
    } else if(!renderTimer){
      renderTimer = setTimeout(()=>{ renderTimer=null; doRender(); }, RENDER_THROTTLE - (now - lastRender));
    }
  }
  function doRender(){
    lastRender = performance.now();
    ansEl.innerHTML = renderSafeMarkdown(shown) + `<span class="tw-caret"></span>`;
    ansEl.scrollIntoView({behavior:"auto", block:"end"});
  }

  function tick(){
    if(buffer.length){
      // 一次吐一個字（CJK 友善；可一次吐 1 字維持中速觀感）
      shown += buffer[0];
      buffer = buffer.slice(1);
      scheduleRender();
    } else if(inputDone){
      stop();
      finalRender();
      onComplete && onComplete();
      return;
    }
    typeTimer = setTimeout(tick, TYPE_INTERVAL);
  }

  function finalRender(){
    // 收尾：完整渲染（含被藏起來的表格/未閉合修正後內容）+ 移除游標
    ansEl.innerHTML = renderSafeMarkdown(shown);
  }
  function stop(){
    if(typeTimer){ clearTimeout(typeTimer); typeTimer=null; }
    if(renderTimer){ clearTimeout(renderTimer); renderTimer=null; }
  }

  return {
    push(text){ buffer += text; },
    finish(cb){ inputDone = true; onComplete = cb; },   // 後端 token 完，等緩衝吐完
    start(){ tick(); },
    abort(){ stop(); },
    getText(){ return shown + buffer; },
  };
}
```

### Step 4.3 — 驗證

- 純前端單元式驗證（無框架，用 console）：`browser_navigate` 後 console 執行：
  ```js
  // 切到 QA，建一個 bot 氣泡測打字機
  setMode('qa');
  const b=document.createElement('div'); b.className='bubble bot';
  const a=document.createElement('div'); a.className='ans'; b.appendChild(a);
  document.getElementById('qa-convo').appendChild(b);
  const tw=createTypewriter(a); tw.start();
  tw.push('政大有以下相關課程：\n\n');
  tw.push('| 課名 | 老師 |\n| --- | --- |\n| 資料結構 | 王 |\n\n');
  tw.push('其中 **資料結構** 最推薦。');
  setTimeout(()=>tw.finish(()=>console.log('done')), 1500);
  ```
  預期：文字逐字出現、cyan 游標閃爍；表格整塊在收尾完整出現（不半截）；`**` 未閉合時不會把後文吃成粗體；finish 後游標消失。
- `safeMarkdownPrefix` 邊界：console 驗 `safeMarkdownPrefix('**未閉合')` 應回不含 `**` 的字串；`safeMarkdownPrefix('文字\n\n| a | b |\n| - | - |')`（表格未收尾）應砍掉表格段。
- 截圖 viewport 1280 / 768 / 375：打字中、表格出現、爆版檢查。
- 不需 commit（見 Task 6）。

---

## Task 5：app.js — 問答 SSE 主路徑接打字機 + fallback client 打字機

**Files**
- Modify `C:\side\NCCU-poc\frontend\app.js`（改寫 `askQuestion` 行 415-449；`appendBotBubble` 行 375-404 重用，新增僅渲染 citations/followup 的輔助）

### Step 5.1 — 抽出「只渲染 citations + followup」的輔助（放在 `appendBotBubble` 行 404 之後）

打字機打完後要在同一個 bot 氣泡尾端淡入引用與追問，需把既有 `appendBotBubble` 內的 citations/followup HTML 組裝抽成可重用函式：

```js
// 組 citations + followup 的 HTML（供打字機收尾後追加）
function buildCitesAndFollowups(data){
  let html = "";
  if(Array.isArray(data.citations) && data.citations.length){
    let cites = `<div class="cites"><div class="cites-label">參考課綱</div>`;
    data.citations.forEach((c,i)=>{
      cites += `<a class="cite" href="${escHtml(c.syllabus_url)}" target="_blank" rel="noopener noreferrer">`
        + `<span class="num">${i+1}</span>`
        + `<span class="cinfo"><span class="cn">${escHtml(c.name)}</span><span class="cd">${escHtml(c.department)} · ${escHtml(c.teacher)}</span></span>`
        + `<span class="arrow">查看 →</span></a>`;
    });
    cites += `</div>`;
    html += cites;
  }
  if(Array.isArray(data.followup_suggestions) && data.followup_suggestions.length){
    let fu = `<div class="followups"><div class="followups-label">你可能想問</div><div class="fu-row">`;
    data.followup_suggestions.forEach(s=>{ fu += `<span class="fu-chip" data-q="${escHtml(s)}">${escHtml(s)}</span>`; });
    fu += `</div></div>`;
    html += fu;
  }
  return html;
}

// 把 cites/followup 淡入到指定 bot 氣泡，並綁定 chip 點擊
function attachCitesAndFollowups(bubbleEl, data){
  const html = buildCitesAndFollowups(data);
  if(!html) return;
  const wrap = document.createElement("div");
  wrap.className = "qa-tail fade-in";
  wrap.innerHTML = html;
  bubbleEl.appendChild(wrap);
  wrap.querySelectorAll(".fu-chip").forEach(chip=>{
    chip.addEventListener("click", ()=>{ if(!qaBusy) askQuestion(chip.dataset.q); });
  });
  bubbleEl.scrollIntoView({behavior:"smooth", block:"end"});
}
```

### Step 5.2 — 改寫 `askQuestion` 為 SSE dispatcher（取代行 415-449）

把 app.js 行 415-449（`async function askQuestion`）整段替換成：`askQuestion`（建氣泡、開串流）、`startStreamQa`、`askQuestionFallback`（原 POST 邏輯，client 端打字機播放）。

```js
let _qaEs = null;
function closeQaEs(){ if(_qaEs){ try{_qaEs.close();}catch(_){} _qaEs = null; } }

function askQuestion(question){
  qaBusy = true;
  qaSend.disabled = true;
  qaEmpty.hidden = true;
  qaInput.value = "";
  appendUserBubble(question);

  // 建立 bot 氣泡（含 .ans 給打字機寫）
  const bubble = document.createElement("div");
  bubble.className = "bubble bot";
  const ans = document.createElement("div");
  ans.className = "ans";
  bubble.appendChild(ans);
  qaConvo.appendChild(bubble);
  bubble.scrollIntoView({behavior:"smooth", block:"end"});

  startStreamQa(question, bubble, ans);
}

function finishQaTurn(){
  qaBusy = false;
  qaSend.disabled = !qaInput.value.trim();
  qaInput.focus();
}

function startStreamQa(question, bubble, ans){
  closeQaEs();
  const tw = createTypewriter(ans);
  tw.start();
  let firstEvent = false, settled = false;
  const sid = qaSessionId ? `&session_id=${encodeURIComponent(qaSessionId)}` : "";
  const url = `${CONFIG.API_URL}/qa/stream?question=${encodeURIComponent(question)}${sid}`;

  const guard = setTimeout(()=>{
    if(!firstEvent && !settled){
      settled = true; closeQaEs(); tw.abort();
      askQuestionFallback(question, bubble, ans);
    }
  }, 8000);

  let es;
  try{ es = new EventSource(url); }
  catch(e){ clearTimeout(guard); tw.abort(); askQuestionFallback(question, bubble, ans); return; }
  _qaEs = es;

  es.addEventListener("token", (ev)=>{
    firstEvent = true;
    let d; try{ d = JSON.parse(ev.data); }catch(_){ return; }
    if(d && typeof d.text === "string") tw.push(d.text);
  });

  es.addEventListener("done", (ev)=>{
    settled = true; clearTimeout(guard); closeQaEs();
    let data; try{ data = JSON.parse(ev.data); }catch(_){ data = {}; }
    qaSessionId = data.session_id || qaSessionId;
    qaTurnCount = data.turn_number || (qaTurnCount + 1);
    qaSessionLabel.textContent = `SESSION · 第 ${qaTurnCount} 輪對話`;
    // 等緩衝吐完字 → 淡入 citations/followup
    tw.finish(()=>{ attachCitesAndFollowups(bubble, data); finishQaTurn(); });
  });

  es.addEventListener("error", (ev)=>{
    if(ev && typeof ev.data === "string" && ev.data.length){
      // 自訂 error 事件
      settled = true; clearTimeout(guard); closeQaEs(); tw.abort();
      let d; try{ d = JSON.parse(ev.data); }catch(_){ d = {}; }
      if(window.Sentry) Sentry.captureMessage(`qa stream error: ${d.error_type||"unknown"}`);
      ans.innerHTML = `<span style="color:#ff9b9b">查詢失敗：${escHtml(d.message || "服務暫時繁忙")}。請稍後再試。</span>`;
      finishQaTurn();
      return;
    }
    if(settled) return;
    clearTimeout(guard); closeQaEs(); tw.abort();
    if(!firstEvent){ askQuestionFallback(question, bubble, ans); }  // proxy 擋 → fallback
    else { ans.innerHTML = renderSafeMarkdown(tw.getText()) + `<span style="color:#ff9b9b">（連線中斷）</span>`; finishQaTurn(); }
  });
}

// fallback：POST /qa 拿完整答案 → client 端逐字播放
async function askQuestionFallback(question, bubble, ans){
  try{
    const resp = await fetch(`${CONFIG.API_URL}/qa`, {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({question, session_id: qaSessionId}),
    });
    if(!resp.ok){
      const err = await resp.json().catch(()=>({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    const data = await resp.json();
    qaSessionId = data.session_id;
    qaTurnCount = data.turn_number || (qaTurnCount + 1);
    qaSessionLabel.textContent = `SESSION · 第 ${qaTurnCount} 輪對話`;
    // client 端打字機播放完整答案
    const tw = createTypewriter(ans);
    tw.start();
    tw.push(data.answer || "");
    tw.finish(()=>{ attachCitesAndFollowups(bubble, data); finishQaTurn(); });
  }catch(e){
    if(window.Sentry) Sentry.captureException(e);
    ans.innerHTML = `<span style="color:#ff9b9b">查詢失敗：${escHtml(e.message)}。請稍後再試。</span>`;
    finishQaTurn();
  }
}
```

> 注意：既有 `appendBotBubble`（行 375-404）與 `appendLoadingBubble`（行 406-413）改為**不再被串流路徑呼叫**（保留供其他地方/回溯相容；可不刪）。`showNoMatch`（行 290-293）內 `qaInput.dispatchEvent` 流程不受影響。

### Step 5.3 — style.css：打字游標 + tail 淡入

在 style.css Q&A 區（`.bubble.bot .ans` 行 221 附近，建議放於行 227 後）新增：

```css
/* 打字機游標 + 收尾淡入 */
.tw-caret{display:inline-block;width:2px;height:1.05em;margin-left:2px;vertical-align:-2px;background:var(--cyan);border-radius:1px;animation:twblink 1s steps(1) infinite}
@keyframes twblink{0%,50%{opacity:1}50.01%,100%{opacity:0}}
.qa-tail.fade-in{animation:tailfade .45s ease both}
@keyframes tailfade{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
/* 串流中表格整塊淡入（marked 產出表格時觸發）*/
.bubble.bot .ans table{animation:tailfade .35s ease both}
```

### Step 5.4 — 驗證

- Playwright E2E（需後端 `/qa/stream`；未就緒時用 console 模擬 Task 4.3 + fallback 路徑）：
  - 桌機 1280：問「哪些課教 Python？」→ 逐字打字、游標閃爍、打完才淡入 citations + followup chips。截圖打字中與完成。
  - 多輪追問：點 followup chip → 帶 session_id 串流接續。
  - 表格題（如「列表比較 X 與 Y 課」）：表格整塊淡入不半截爆版。
  - 離題 / 503：error 事件 → 紅字錯誤、不卡死。
- fallback：console 把 `CONFIG.API_URL` 暫指錯誤埠，確認 8s guard → `askQuestionFallback` → client 打字機播放完整答案。
- 截圖 viewport 375 / 768 / 1280（手機 composer 固定底部不遮氣泡）。
- 不需 commit（見 Task 6）。

---

## Task 6：整合驗證 + 單一 commit

**Files**：無新增；驗證 + commit 全部改動。

### Step 6.1 — 跨裝置整合截圖（Playwright）

依序在 viewport 375 / 768 / 1280 下，對推薦（清單內 / 清單外 / no_match）與問答（單輪 / 多輪 / 表格 / 錯誤）走完整 e2e（真後端、真 Gemini），逐一截圖，確認：

- 推薦 5 階段逐一亮、進度條躍進、完成轉綠、各 viewport 不破版。
- 問答打字機逐字、表格不爆版、打完才出引用、composer 不遮內容。
- Console 無 JS 錯誤；`EventSource` 在收最終事件後關閉（network 面板確認連線結束、無重連）。

> 後端尚未就緒時：本 Task 的真 e2e 留待後續整合 Wave；本計畫範圍先以 console 模擬 + UI 截圖驗證前端邏輯與樣式，並在 commit message 註明「待後端 stream 端點上線後跑真 e2e」。

### Step 6.2 — commit

```
feat(frontend): SSE streaming UX — 推薦 5 階段即時進度 + 問答打字機

- index.html: loading 改 5 階段 stepper；資源 ?v=3 cache-bust
- style.css: stepper 三態(done/active/pending)+spinner+進度條轉綠、打字游標、表格淡入、RWD
- app.js: /recommend/stream EventSource + easing 90%-hold + es.close + fallback POST；
          /qa/stream 打字機(34cps 固定節奏 + markdown 安全逐區塊渲染) + fallback client 打字機

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

---

## 風險與需與後端對齊的點

1. **`error` 事件 vs EventSource 連線層 error 同名**：SSE 自訂 `event: error` 與 `EventSource` 內建 `error`（連線失敗）都觸發 `addEventListener("error")`。本計畫用 `ev.data` 是否為非空字串區分（自訂事件有 data、連線層無）。**後端送錯誤務必用 `event: error` 且帶 JSON data**，否則前端會誤判成連線失敗而降級。
2. **`done` payload 欄位命名**：須為 `citations` / `followup_suggestions` / `session_id` / `turn_number`（與既有渲染一致）。後端對齊。
3. **首事件逾時 8s**：proxy 緩衝時整批事件可能延後到達 → 可能誤觸發 fallback。8s 是估值，後端上線後實測（`curl -N`）調整；後端應盡快送第一個 `stage start`（understand）以「佔位」避免逾時。
4. **心跳**：`: keep-alive` 註解行 EventSource 自動忽略，前端無需處理；但後端須每 ≤15s 送一次防 idle timeout。
5. **seed 來源**：原 `POST /recommend` 不帶 seed（後端亂數）；串流改前端產生 seed 帶入 query，換一批每次新 seed。後端 `/recommend/stream` 須接受並使用 `seed`。
