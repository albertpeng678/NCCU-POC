// app.js — NCCU Course Map frontend logic
import { createPaginationState, nextBatch, appendPool, groupBatch } from "./pagination.js";
import { createProgressiveRenderer, drainCount } from "./progressive-md.js";

const CONFIG = {
  // Local dev default; overwrite before Railway deploy.
  // window.__API_URL__ 由 Playwright e2e addInitScript 注入，可覆蓋指定 backend port（qa-with-db/qa-no-db）。
  API_URL: (window.__API_URL__ ||
    ((location.hostname === "localhost" || location.hostname === "127.0.0.1")
      ? "http://localhost:8000"
      : "")),  // 部署同源：前後端同網址 → 用相對路徑（fetch("/recommend")），免綁死網域、零 CORS
  // Sentry DSN（公開可安全放原始碼）；留空則停用。Sentry 專案 nccu-poc（org albert-ar）。
  SENTRY_DSN: "https://eb5ebaf502bf1590ef5f87da67282518@o4511451335622656.ingest.us.sentry.io/4511504292904960",
};

// Sentry：DSN 有設且 SDK 載入才啟用（錯誤監控 + tracing）
if (window.Sentry && CONFIG.SENTRY_DSN) {
  Sentry.init({
    dsn: CONFIG.SENTRY_DSN,
    tracesSampleRate: 1.0,
    environment: (location.hostname === "localhost" || location.hostname === "127.0.0.1") ? "development" : "production",
  });
}

// ---------- DOM refs ----------
const menuBtn   = document.getElementById("menu-btn");
const offcanvas = document.getElementById("offcanvas");
const scrim     = document.getElementById("scrim");
const ocClose   = document.getElementById("oc-close");
const ocSearch  = document.getElementById("oc-search");
const ocList    = document.getElementById("oc-list");

const input     = document.getElementById("career-input");
const dropdown  = document.getElementById("autocomplete-list");
const searchBtn = document.getElementById("search-btn");
const hotPicks  = document.getElementById("hot-picks");

const loadingEl = document.getElementById("loading");
const errorEl   = document.getElementById("error");
const errorMsg  = document.getElementById("error-msg");
const resultsEl = document.getElementById("results");
const resCareer = document.getElementById("results-career");
const resLatency= document.getElementById("results-latency");
const groupsEl  = document.getElementById("groups");

// careers.js 以普通 script 載入並掛 window；module scope 讀回
const CAREERS = window.CAREERS || [];
const HOT_PICKS = window.HOT_PICKS || [];

// ---------- State ----------
let selectedCareer = null;
let lastCareer = null;   // 上次實際送出的職涯（含清單外自由輸入），供「換一批」沿用
let activeIdx = -1;
let pageState = null;    // 分頁 state（整池 + shown）
let lastNotice = null;   // 清單外職涯誠實說明（換批沿用）
let rerolling = false;   // 續池/切批進行中（去抖，避免 rapid double-click）

// ---------- Util ----------
function escHtml(s){
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

// ---------- Offcanvas ----------
function openOffcanvas(){
  offcanvas.classList.add("open");
  offcanvas.setAttribute("aria-hidden","false");
  menuBtn.setAttribute("aria-expanded","true");
  scrim.hidden = false;
  requestAnimationFrame(()=>scrim.classList.add("show"));
  ocSearch.value = "";
  renderOcList(CAREERS);
  ocSearch.focus();
}
function closeOffcanvas(){
  offcanvas.classList.remove("open");
  offcanvas.setAttribute("aria-hidden","true");
  menuBtn.setAttribute("aria-expanded","false");
  scrim.classList.remove("show");
  setTimeout(()=>{ scrim.hidden = true; }, 300);
}
menuBtn.addEventListener("click", openOffcanvas);
ocClose.addEventListener("click", closeOffcanvas);
scrim.addEventListener("click", closeOffcanvas);
document.addEventListener("keydown", (e)=>{
  if(e.key === "Escape"){
    if(offcanvas.classList.contains("open")) closeOffcanvas();
    else if(!dropdown.hidden){ dropdown.hidden = true; }
  }
});

function renderOcList(items){
  ocList.innerHTML = "";
  items.forEach(c=>{
    const div = document.createElement("div");
    div.className = "oc-item";
    div.setAttribute("role","option");
    div.textContent = c;
    div.addEventListener("click", ()=>{
      selectCareer(c);
      closeOffcanvas();
    });
    ocList.appendChild(div);
  });
}
ocSearch.addEventListener("input", ()=>{
  const q = ocSearch.value.trim().toLowerCase();
  renderOcList(q ? CAREERS.filter(c=>c.toLowerCase().includes(q)) : CAREERS);
});

// ---------- Autocomplete ----------
function filterCareers(q){
  if(!q.trim()) return [];
  const s = q.toLowerCase();
  return CAREERS.filter(c=>c.toLowerCase().includes(s)).slice(0,8);
}
function renderDropdown(items){
  dropdown.innerHTML = "";
  if(!items.length){ dropdown.hidden = true; input.setAttribute("aria-expanded","false"); return; }
  items.forEach((item,idx)=>{
    const li = document.createElement("li");
    li.className = "autocomplete-item";
    li.setAttribute("role","option");
    li.id = `ac-opt-${idx}`;
    li.textContent = item;
    li.addEventListener("mousedown",(e)=>{ e.preventDefault(); selectCareer(item); });
    dropdown.appendChild(li);
  });
  dropdown.hidden = false;
  input.setAttribute("aria-expanded","true");
  activeIdx = -1;
}
function selectCareer(career){
  selectedCareer = career;
  input.value = career;
  dropdown.hidden = true;
  input.setAttribute("aria-expanded","false");
  searchBtn.disabled = false;
}
function clearSelection(){ selectedCareer = null; searchBtn.disabled = true; }

input.addEventListener("input", ()=>{
  clearSelection();
  renderDropdown(filterCareers(input.value));
  // 允許清單外職涯：只要輸入非空就能送出（後端會 LLM 推導或溫和導向問答）
  searchBtn.disabled = !input.value.trim();
});
input.addEventListener("keydown",(e)=>{
  const items = dropdown.querySelectorAll(".autocomplete-item");
  if(e.key === "ArrowDown" && items.length){
    e.preventDefault(); activeIdx = Math.min(activeIdx+1, items.length-1);
    items.forEach((el,i)=>el.setAttribute("aria-selected", i===activeIdx));
    input.setAttribute("aria-activedescendant", `ac-opt-${activeIdx}`);
  } else if(e.key === "ArrowUp" && items.length){
    e.preventDefault(); activeIdx = Math.max(activeIdx-1, 0);
    items.forEach((el,i)=>el.setAttribute("aria-selected", i===activeIdx));
    input.setAttribute("aria-activedescendant", `ac-opt-${activeIdx}`);
  } else if(e.key === "Enter"){
    e.preventDefault();
    if(activeIdx >= 0 && items[activeIdx]) selectCareer(items[activeIdx].textContent);
    else { const c = selectedCareer || input.value.trim(); if(c) fetchRecommendation(c); }
  }
});
document.addEventListener("click",(e)=>{
  if(!e.target.closest(".autocomplete-wrapper")) dropdown.hidden = true;
});

// ---------- Hot pick pills ----------
HOT_PICKS.forEach(c=>{
  const b = document.createElement("button");
  b.className = "pill"; b.type = "button"; b.textContent = c;
  b.setAttribute("aria-label", `選擇職涯：${c}`);
  b.addEventListener("click", ()=>{ selectCareer(c); fetchRecommendation(c); });
  hotPicks.appendChild(b);
});

// ---------- Render results ----------
function renderReason(reason){
  // reason: { lead: string, points: [{term, detail}] }  (structured)
  // fallback: plain string
  if(typeof reason === "string"){
    return `<div class="creason">${escHtml(reason)}</div>`;
  }
  const lead = reason && reason.lead ? `<div class="lead">${escHtml(reason.lead)}</div>` : "";
  const pts = (reason && Array.isArray(reason.points)) ? reason.points : [];
  const lis = pts.map(p=>`<li><strong>${escHtml(p.term)}</strong>：${escHtml(p.detail)}</li>`).join("");
  return `<div class="creason">${lead}${lis?`<ul>${lis}</ul>`:""}</div>`;
}
function renderCard(course){
  const card = document.createElement("div");
  card.className = "card";
  card.setAttribute("data-course-id", course.course_id || "");
  card.setAttribute("data-group", course.group || "");
  card.innerHTML = `
    <div class="cname">${escHtml(course.name)}</div>
    <div class="cmeta">${escHtml(course.department)} · ${escHtml(course.teacher)} · ${course.credits} 學分</div>
    ${renderReason(course.reason)}
    <a class="clink" href="${escHtml(course.syllabus_url)}" target="_blank" rel="noopener noreferrer">查看課綱 →</a>`;
  return card;
}
const GROUP_META = [
  {key:"core",        idx:"01", title:"核心技能", desc:"直接對應職涯核心能力"},
  {key:"supporting",  idx:"02", title:"輔助技能", desc:"強化周邊能力、增加競爭力"},
  {key:"extended",    idx:"03", title:"延伸視野", desc:"跨域拓展、差異化視角"},
];
function renderResults(data){
  stopLoading();
  loadingEl.hidden = true; errorEl.hidden = true;
  const nm = document.getElementById("no-match"); if(nm) nm.hidden = true;
  resCareer.textContent = data.career;
  resLatency.textContent = data.latency_ms ? `GENERATED ${(data.latency_ms/1000).toFixed(1)}s` : "";
  // 存整池並重置分頁
  pageState = createPaginationState(data.courses || [], data.batch_size || 10);
  lastNotice = data.notice || null;
  renderBatch(nextBatch(pageState));   // 首批
  resultsEl.hidden = false;
  resultsEl.scrollIntoView({behavior:"smooth", block:"start"});
}

// 渲染「一批」課程：依 group 分三區塊（沿用 GROUP_META）
function renderBatch(batch){
  groupsEl.innerHTML = "";
  if(lastNotice){
    const n = document.createElement("p");
    n.className = "result-notice";
    n.textContent = lastNotice;
    groupsEl.appendChild(n);
  }
  if(!batch || !batch.length){
    const p = document.createElement("p");
    p.className = "result-notice";
    p.textContent = "已涵蓋主要推薦課程。";
    groupsEl.appendChild(p);
    return;
  }
  const grouped = groupBatch(batch);
  GROUP_META.forEach(g=>{
    const list = grouped[g.key] || [];
    if(!list.length) return;
    const block = document.createElement("div");
    block.className = "group";
    block.setAttribute("data-group-key", g.key);
    block.setAttribute("role", "region");
    block.setAttribute("aria-label", g.title);
    block.innerHTML = `
      <div class="group-title"><span class="idx">${g.idx}</span><h3>${g.title}</h3></div>
      <p class="group-desc">${g.desc}</p>
      <div class="cards" data-testid="group-cards-${g.key}"></div>`;
    const cardsEl = block.querySelector(".cards");
    list.forEach(c=>cardsEl.appendChild(renderCard(c)));
    groupsEl.appendChild(block);
  });
}

// ---------- States ----------
// ---------- 推薦 5 階段（SSE 串流）----------
const REC_STAGES = [
  {key:"understand", label:"理解你的職涯方向"},
  {key:"retrieve",   label:"檢索全校課綱"},
  {key:"filter",     label:"篩選候選課程"},
  {key:"compose",    label:"編排推薦組合與理由"},
  {key:"finalize",   label:"整理課程資訊"},
];
// 每階段在進度條佔的「累積上限 %」（誠實：done 才補到該段終點；compose 封頂 94%，result 真到才 100%）
const STAGE_END = {understand:4, retrieve:56, filter:60, compose:94, finalize:100};
// 各階段預估剩餘秒（用於「預估還需約 N 秒」）。校準至 fan-out 實測：retrieve(並行檢索)~44s、compose(整池標註)~32s。
const STAGE_SEC = {understand:3, retrieve:44, filter:1, compose:32, finalize:2};
let _recEs = null;          // 當前 EventSource
let _stageRaf = null;       // easing 動畫 rAF handle
let _curStageIdx = -1;      // 目前 active 階段 index

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
  if(!r.steps.length) return;
  _curStageIdx = -1;
  r.card && r.card.classList.remove("done");
  if(r.bar){ r.bar.classList.remove("done"); r.bar.style.transition = "none"; r.bar.style.width = "0%"; }
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
function showError(msg){ stopLoading(); loadingEl.hidden = true; resultsEl.hidden = true; errorEl.hidden = false; errorMsg.textContent = msg; }

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

// 清單外且查無 → 溫和訊息卡 + 一鍵導向問答（不卡死）
function showNoMatch(career, message){
  stopLoading();
  loadingEl.hidden = true; errorEl.hidden = true; resultsEl.hidden = true;
  let card = document.getElementById("no-match");
  if(!card){
    card = document.createElement("section");
    card.id = "no-match"; card.className = "no-match-card";
    resultsEl.parentNode.insertBefore(card, resultsEl);
  }
  card.innerHTML = `<p class="nm-msg"></p>
    <button class="nm-cta" type="button">用問答模式問我 →</button>`;
  card.querySelector(".nm-msg").textContent = message;
  card.querySelector(".nm-cta").addEventListener("click", ()=>{
    card.hidden = true;
    setMode("qa");
    qaInput.value = `想當${career}，政大有哪些相關課程或能力可以培養？`;
    qaInput.dispatchEvent(new Event("input"));
    qaInput.focus();
  });
  card.hidden = false;
  card.scrollIntoView({behavior:"smooth", block:"center"});
}

searchBtn.addEventListener("click", ()=>{ const c = selectedCareer || input.value.trim(); if(c) fetchRecommendation(c); });

// 換一批：池中有 → 純前端切片（0 網路請求）；池乾 → 以新 seed 呼叫 /recommend/stream 續池
const rerollBtn = document.getElementById("reroll-btn");
rerollBtn.addEventListener("click", ()=>{
  if(rerolling) return;                 // 去抖：處理中忽略重複點擊
  if(!pageState){ if(lastCareer) fetchRecommendation(lastCareer); return; }
  const batch = nextBatch(pageState);
  if(batch){                            // 池中還有 → 純前端切片，0 網路請求
    renderBatch(batch);
    resultsEl.scrollIntoView({behavior:"smooth", block:"start"});
    return;
  }
  // 池乾 → 以新 seed 呼叫 /recommend/stream 續池
  if(!lastCareer) return;
  rerolling = true;
  rerollBtn.disabled = true;
  continuePool(lastCareer);
});

// 續池：新 seed 取一池 → append 去重 → 切下一批；UI 沿用 streaming stepper
function continuePool(career){
  closeRecEs();
  const seed = Math.floor(Math.random() * 1e9);
  const url = `${CONFIG.API_URL}/recommend/stream?career=${encodeURIComponent(career)}&seed=${seed}`;
  showLoadingShell();
  let firstEvent = false, settled = false;
  const guard = setTimeout(()=>{ if(!firstEvent && !settled){ closeRecEs(); continuePoolFallback(career); } }, 8000);
  _loadTimers.push(guard);
  let es;
  try{ es = new EventSource(url); }
  catch(e){ clearTimeout(guard); continuePoolFallback(career); return; }
  _recEs = es;
  es.addEventListener("stage", (ev)=>{
    firstEvent = true;
    let d; try{ d = JSON.parse(ev.data); }catch(_){ return; }
    const idx = (d.n|0) - 1;
    if(idx < 0 || idx >= REC_STAGES.length) return;
    if(d.status === "start") setStageActive(idx); else if(d.status === "done") setStageDone(idx);
  });
  es.addEventListener("result", (ev)=>{
    settled = true; clearTimeout(guard); closeRecEs();
    let data; try{ data = JSON.parse(ev.data); }catch(e){ finishContinue(null); return; }
    finishProgress();
    setTimeout(()=>finishContinue(data), 350);
  });
  es.addEventListener("no_match", ()=>{ settled = true; clearTimeout(guard); closeRecEs(); finishContinue(null); });
  es.addEventListener("error", (ev)=>{
    if(ev && typeof ev.data === "string" && ev.data.length){
      settled = true; clearTimeout(guard); closeRecEs(); finishContinue(null); return;
    }
    if(settled) return;
    clearTimeout(guard); closeRecEs();
    if(!firstEvent) continuePoolFallback(career); else finishContinue(null);
  });
}

async function continuePoolFallback(career){
  try{
    const resp = await fetch(`${CONFIG.API_URL}/recommend`, {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({career}),
    });
    if(!resp.ok) throw new Error(`HTTP ${resp.status}`);
    finishContinue(await resp.json());
  }catch(e){ finishContinue(null); }
}

// 續池收尾：append 去重 → 切下一批顯示；失敗則顯示「已涵蓋主要推薦」
function finishContinue(data){
  loadingEl.hidden = true;
  resultsEl.hidden = false;
  if(data && Array.isArray(data.courses) && data.courses.length){
    appendPool(pageState, data.courses);
    const batch = nextBatch(pageState);
    renderBatch(batch);   // batch 可能 null → renderBatch 顯示「已涵蓋」
  } else {
    renderBatch(null);
  }
  rerolling = false;
  rerollBtn.disabled = false;
  resultsEl.scrollIntoView({behavior:"smooth", block:"start"});
}

// ========== Q&A mode ==========
const chipRecommend = document.getElementById("chip-recommend");
const chipQa        = document.getElementById("chip-qa");
const modeRecommend = document.getElementById("mode-recommend");
const modeQa        = document.getElementById("mode-qa");
const newChatBtn    = document.getElementById("new-chat-btn");
const qaComposer    = document.getElementById("qa-composer");
const qaConvo       = document.getElementById("qa-convo");
const qaEmpty       = document.getElementById("qa-empty");
const qaInput       = document.getElementById("qa-input");
const qaSend        = document.getElementById("qa-send");
const qaSessionLabel= document.getElementById("qa-session-label");

let qaSessionId = null;
let qaTurnCount = 0;
let qaBusy = false;

function setMode(mode){
  const qa = mode === "qa";
  chipRecommend.classList.toggle("active", !qa);
  chipQa.classList.toggle("active", qa);
  chipRecommend.setAttribute("aria-selected", String(!qa));
  chipQa.setAttribute("aria-selected", String(qa));
  modeRecommend.hidden = qa;
  modeQa.hidden = !qa;
  qaComposer.hidden = !qa;
  newChatBtn.hidden = !qa;
  document.body.classList.toggle("qa-active", qa);
  if(qa) qaInput.focus();
}
chipRecommend.addEventListener("click", ()=> setMode("recommend"));
chipQa.addEventListener("click", ()=> setMode("qa"));

// 推薦結果底部 CTA：把使用者導向問答模式
const toQaLink = document.getElementById("to-qa-link");
toQaLink.addEventListener("click", (e)=>{ e.preventDefault(); setMode("qa"); });

qaInput.addEventListener("input", ()=>{ qaSend.disabled = !qaInput.value.trim() || qaBusy; });
qaInput.addEventListener("keydown", (e)=>{ if(e.key==="Enter" && qaInput.value.trim() && !qaBusy) askQuestion(qaInput.value.trim()); });
qaSend.addEventListener("click", ()=>{ if(qaInput.value.trim() && !qaBusy) askQuestion(qaInput.value.trim()); });

newChatBtn.addEventListener("click", ()=>{
  qaSessionId = null; qaTurnCount = 0;
  qaConvo.innerHTML = "";
  qaEmpty.hidden = false;
  qaSessionLabel.textContent = "新對話";
});

function renderAnswerHtml(answer){
  // answer 是 Gemini 產生的 Markdown（含表格/粗體）。用 marked 轉 HTML，再經 DOMPurify
  // sanitize（內容來自 LLM，務必防 XSS）。若 CDN 未載入則退回純文字。
  const raw = answer || "";
  if (typeof marked === "undefined" || typeof DOMPurify === "undefined"){
    return `<div class="ans">${escHtml(raw).replace(/\n/g,"<br>")}</div>`;
  }
  const html = marked.parse(raw, {breaks:true, gfm:true});
  const safe = DOMPurify.sanitize(html, {
    ALLOWED_TAGS: ["p","br","strong","em","u","h1","h2","h3","h4","ul","ol","li",
                   "table","thead","tbody","tr","th","td","blockquote","code","pre","a","hr"],
    ALLOWED_ATTR: ["href","target","rel"],
  });
  return `<div class="ans">${safe}</div>`;
}

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
  // 表格視為「完成」只在其後出現空行(\n\n)；單一 \n 不算 → 避免表格落入尾端 live 區被每 tick 重建而頻閃
  const endsClean = /\n[^\S\n]*\n[^\S\n]*$/.test(text);
  if(tailIsTable && !endsClean){
    cut = tailStart >= 0 ? tailStart : 0;   // 整塊表格延後到收尾（空行）
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

// marked + DOMPurify（不含 safe-prefix 處理）；輸入須已是可安全渲染的文字
function mdToHtml(text){
  if (typeof marked === "undefined" || typeof DOMPurify === "undefined"){
    return escHtml(text).replace(/\n/g,"<br>");
  }
  const html = marked.parse(text, {breaks:true, gfm:true});
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS: ["p","br","strong","em","u","h1","h2","h3","h4","ul","ol","li",
                   "table","thead","tbody","tr","th","td","blockquote","code","pre","a","hr"],
    ALLOWED_ATTR: ["href","target","rel"],
  });
}

function renderSafeMarkdown(raw){
  return mdToHtml(safeMarkdownPrefix(raw));   // 不含外層 .ans，由打字機容器負責
}

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

  // 防頻閃：已完成的區塊（以 \n\n 為界）只渲染一次、append 進 committedEl 後不再動它；
  // 只有尾端「打字中」的文字放 liveEl，每 tick 重渲染。→ 已完成的表格不會被每 tick 重建。
  const committedEl = document.createElement("span");
  const liveEl = document.createElement("span");
  // live 尾端改渲染 healed markdown（safeMarkdownPrefix 藏半截表格/未閉合粗體），故不再用 pre-wrap 純文字
  const caretEl = document.createElement("span");
  caretEl.className = "tw-caret";
  ansEl.innerHTML = "";
  ansEl.appendChild(committedEl);
  ansEl.appendChild(liveEl);
  ansEl.appendChild(caretEl);
  let committedLen = 0;   // 已 commit 的 shown 字元數

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
    // 已完成區塊（以 \n\n 為界）append 進 committedEl 並渲染成 markdown（只渲染一次，表格不重建→不閃）
    const boundary = shown.lastIndexOf("\n\n");
    if(boundary > committedLen){
      committedEl.insertAdjacentHTML("beforeend", mdToHtml(shown.slice(committedLen, boundary)));
      committedLen = boundary;
    }
    // live 尾端：渲染 healed markdown（safeMarkdownPrefix 藏半截表格/未閉合粗體）→ 表格 snap-in、不露 raw |、粗體即時
    liveEl.innerHTML = renderSafeMarkdown(shown.slice(committedLen));
    ansEl.scrollIntoView({behavior:"auto", block:"end"});
  }

  function tick(){
    if(buffer.length){
      // backlog 自適應：buffer 大則一次吐多字追上生成，小則逐字（CJK 友善）
      const n = drainCount(buffer.length);
      shown += buffer.slice(0, n);
      buffer = buffer.slice(n);
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
    // 收尾：一次性完整渲染全文（含尾端被藏的內容），移除游標與分段容器
    caretEl.remove();
    ansEl.innerHTML = mdToHtml(shown);
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

function appendUserBubble(text){
  const b = document.createElement("div");
  b.className = "bubble user";
  b.textContent = text;
  qaConvo.appendChild(b);
}

function appendBotBubble(data){
  const b = document.createElement("div");
  b.className = "bubble bot";
  let html = renderAnswerHtml(data.answer || "");
  // citations
  if(Array.isArray(data.citations) && data.citations.length){
    let cites = `<div class="cites" data-testid="qa-citations"><div class="cites-label">參考課綱</div>`;
    data.citations.forEach((c,i)=>{
      cites += `<a class="cite" href="${escHtml(c.syllabus_url)}" target="_blank" rel="noopener noreferrer">`
        + `<span class="num">${i+1}</span>`
        + `<span class="cinfo"><span class="cn">${escHtml(c.name)}</span><span class="cd">${escHtml(c.department)} · ${escHtml(c.teacher)}</span></span>`
        + `<span class="arrow">查看 →</span></a>`;
    });
    cites += `</div>`;
    html += cites;
  }
  // followup chips
  if(Array.isArray(data.followup_suggestions) && data.followup_suggestions.length){
    let fu = `<div class="followups"><div class="followups-label">你可能想問</div><div class="fu-row">`;
    data.followup_suggestions.forEach(s=>{ fu += `<span class="fu-chip" data-q="${escHtml(s)}">${escHtml(s)}</span>`; });
    fu += `</div></div>`;
    html += fu;
  }
  b.innerHTML = html;
  b.querySelectorAll(".fu-chip").forEach(chip=>{
    chip.addEventListener("click", ()=>{ if(!qaBusy) askQuestion(chip.dataset.q); });
  });
  qaConvo.appendChild(b);
  b.scrollIntoView({behavior:"smooth", block:"end"});
}

// 組 citations + followup 的 HTML（供打字機收尾後追加）
function buildCitesAndFollowups(data){
  let html = "";
  if(Array.isArray(data.citations) && data.citations.length){
    let cites = `<div class="cites" data-testid="qa-citations"><div class="cites-label">參考課綱</div>`;
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

function appendLoadingBubble(){
  const b = document.createElement("div");
  b.className = "bubble bot";
  b.id = "qa-loading-bubble";
  b.innerHTML = `<div class="ans loading-text">AI 正在查閱課綱<span class="dots"><i>.</i><i>.</i><i>.</i></span></div>`;
  qaConvo.appendChild(b);
  b.scrollIntoView({behavior:"smooth", block:"end"});
}

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
  bubble.setAttribute("data-testid", "qa-answer");   // e2e locator anchor
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

// 等待期間（首 token 前）在 bot 泡泡內顯示 5 階段垂直 stepper（借鑑參考 bot 的 RAG 等待 UX）
const QA_STAGES = ["理解你的問題", "翻閱課綱知識庫", "比對相關重點", "整理重點段落", "最後潤飾"];
function startQaStages(ans){
  ans.innerHTML = `<ol class="qa-steps">` +
    QA_STAGES.map(s => `<li class="qa-st"><span class="qa-st-mark" aria-hidden="true"></span>${escHtml(s)}…</li>`).join("") +
    `</ol><div class="qa-stbar" aria-hidden="true"><span class="qa-stbar-fill"></span></div>`;
  const items = ans.querySelectorAll(".qa-st");
  const fill = ans.querySelector(".qa-stbar-fill");
  let i = 0;
  const apply = ()=>{
    items.forEach((el, idx)=>{ el.classList.toggle("done", idx < i); el.classList.toggle("active", idx === i); });
    if(fill) fill.style.width = `${Math.min(((i + 0.6) / QA_STAGES.length) * 100, 92)}%`;  // 永不到 100%（誠實，真完成才補滿）
  };
  apply();
  // 時間驅動（QA 後端未暴露真實階段，與參考 bot 同為節奏推進）；推進到最後一階段就停住
  const iv = setInterval(()=>{ if(i < QA_STAGES.length - 1){ i++; apply(); } }, 2600);
  return { stop(){ clearInterval(iv); } };
}

function startStreamQa(question, bubble, ans){
  closeQaEs();
  let tw = null;                       // 延後到首 token 才建打字機；先顯示階段 loader
  const stage = startQaStages(ans);
  let firstEvent = false, settled = false;
  const sid = qaSessionId ? `&session_id=${encodeURIComponent(qaSessionId)}` : "";
  const url = `${CONFIG.API_URL}/qa/stream?question=${encodeURIComponent(question)}${sid}`;

  // 首事件逾時：8s→30s（積極 retry/high-demand 下首 token 可能較久，別誤觸發 fallback；
  // 階段 loader 在此期間提供「持續在動」的視覺，不會像凍住）
  const guard = setTimeout(()=>{
    if(!firstEvent && !settled){
      settled = true; closeQaEs(); stage.stop();
      askQuestionFallback(question, bubble, ans);
    }
  }, 30000);

  let es;
  try{ es = new EventSource(url); }
  catch(e){ clearTimeout(guard); stage.stop(); askQuestionFallback(question, bubble, ans); return; }
  _qaEs = es;

  es.addEventListener("token", (ev)=>{
    let d; try{ d = JSON.parse(ev.data); }catch(_){ return; }
    if(!(d && typeof d.text === "string")) return;
    if(!firstEvent){
      firstEvent = true;
      stage.stop();                 // 停階段 loader
      ans.innerHTML = "";           // 清掉 loader
      // 漸進 markdown：每 token rAF 節流重渲染累積緩衝 → 表格/粗體/標題隨完成即現
      tw = createProgressiveRenderer({ render: (buf)=> ans.innerHTML = renderSafeMarkdown(buf) });
    }
    tw.push(d.text);
  });

  es.addEventListener("done", (ev)=>{
    settled = true; clearTimeout(guard); closeQaEs();
    let data; try{ data = JSON.parse(ev.data); }catch(_){ data = {}; }
    qaSessionId = data.session_id || qaSessionId;
    window.__lastQaSessionId__ = qaSessionId;   // e2e hook: with-DB 測試讀回 session 用
    qaTurnCount = data.turn_number || (qaTurnCount + 1);
    qaSessionLabel.textContent = `SESSION · 第 ${qaTurnCount} 輪對話`;
    const renderFinal = ()=>{
      if(typeof data.answer === "string" && data.answer) ans.innerHTML = renderSafeMarkdown(data.answer);
      attachCitesAndFollowups(bubble, data);
      finishQaTurn();
    };
    if(!tw){ stage.stop(); renderFinal(); return; }   // 沒收到任何 token 就 done → 停階段、直接渲染
    // 等緩衝吐完字 → 用權威 answer 覆蓋已串流文字（套用防幻覺覆寫）→ 淡入 citations/followup
    tw.finish(renderFinal);
  });

  es.addEventListener("error", (ev)=>{
    if(ev && typeof ev.data === "string" && ev.data.length){
      // 自訂 error 事件
      settled = true; clearTimeout(guard); closeQaEs(); stage.stop(); tw && tw.abort();
      let d; try{ d = JSON.parse(ev.data); }catch(_){ d = {}; }
      if(window.Sentry) Sentry.captureMessage(`qa stream error: ${d.error_type||"unknown"}`);
      ans.innerHTML = `<span style="color:#ff9b9b">查詢失敗：${escHtml(d.message || "服務暫時繁忙")}。請稍後再試。</span>`;
      finishQaTurn();
      return;
    }
    if(settled) return;
    clearTimeout(guard); closeQaEs(); stage.stop(); tw && tw.abort();
    if(!firstEvent){ askQuestionFallback(question, bubble, ans); }  // proxy 擋/連線失敗 → fallback
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
    window.__lastQaSessionId__ = qaSessionId;   // e2e hook: with-DB 測試讀回 session 用
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
