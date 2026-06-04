// app.js — NCCU Course Map frontend logic
const CONFIG = {
  // Local dev default; overwrite before Railway deploy.
  API_URL: (location.hostname === "localhost" || location.hostname === "127.0.0.1")
    ? "http://localhost:8000"
    : "https://nccu-backend.up.railway.app",
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

// ---------- State ----------
let selectedCareer = null;
let lastCareer = null;   // 上次實際送出的職涯（含清單外自由輸入），供「換一批」沿用
let activeIdx = -1;

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
  loadingEl.hidden = true; errorEl.hidden = true;
  const nm = document.getElementById("no-match"); if(nm) nm.hidden = true;
  resCareer.textContent = data.career;
  resLatency.textContent = data.latency_ms ? `GENERATED ${(data.latency_ms/1000).toFixed(1)}s` : "";
  groupsEl.innerHTML = "";
  // 清單外職涯的誠實說明條（可轉移能力課程）
  if(data.notice){
    const n = document.createElement("p");
    n.className = "result-notice";
    n.textContent = data.notice;
    groupsEl.appendChild(n);
  }
  GROUP_META.forEach(g=>{
    const list = (data.groups && data.groups[g.key]) || [];
    if(!list.length) return;
    const block = document.createElement("div");
    block.className = "group";
    block.innerHTML = `
      <div class="group-title"><span class="idx">${g.idx}</span><h3>${g.title}</h3></div>
      <p class="group-desc">${g.desc}</p>
      <div class="cards"></div>`;
    const cardsEl = block.querySelector(".cards");
    list.forEach(c=>cardsEl.appendChild(renderCard(c)));
    groupsEl.appendChild(block);
  });
  resultsEl.hidden = false;
  resultsEl.scrollIntoView({behavior:"smooth", block:"start"});
}

// ---------- States ----------
function showLoading(){ resultsEl.hidden = true; errorEl.hidden = true; loadingEl.hidden = false; loadingEl.scrollIntoView({behavior:"smooth",block:"center"}); }
function showError(msg){ loadingEl.hidden = true; resultsEl.hidden = true; errorEl.hidden = false; errorMsg.textContent = msg; }

// ---------- API ----------
async function fetchRecommendation(career){
  lastCareer = career;            // 供「換一批」沿用（含清單外職涯）
  showLoading();
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
    if(data && data.no_match){ showNoMatch(career, data.message || "目前沒有找到相關課程。"); return; }
    renderResults(data);
  }catch(e){
    showError(`查詢失敗：${e.message}。請稍後再試。`);
  }
}

// 清單外且查無 → 溫和訊息卡 + 一鍵導向問答（不卡死）
function showNoMatch(career, message){
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

// 換一批：以「上次送出的職涯」重呼叫（後端每次新亂數 seed → 不同一批課；清單外職涯也適用）
const rerollBtn = document.getElementById("reroll-btn");
rerollBtn.addEventListener("click", ()=>{ if(lastCareer) fetchRecommendation(lastCareer); });

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

function appendLoadingBubble(){
  const b = document.createElement("div");
  b.className = "bubble bot";
  b.id = "qa-loading-bubble";
  b.innerHTML = `<div class="ans loading-text">AI 正在查閱課綱<span class="dots"><i>.</i><i>.</i><i>.</i></span></div>`;
  qaConvo.appendChild(b);
  b.scrollIntoView({behavior:"smooth", block:"end"});
}

async function askQuestion(question){
  qaBusy = true;
  qaSend.disabled = true;
  qaEmpty.hidden = true;
  qaInput.value = "";
  appendUserBubble(question);
  appendLoadingBubble();
  try{
    const resp = await fetch(`${CONFIG.API_URL}/qa`, {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({question, session_id: qaSessionId}),
    });
    document.getElementById("qa-loading-bubble")?.remove();
    if(!resp.ok){
      const err = await resp.json().catch(()=>({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    const data = await resp.json();
    qaSessionId = data.session_id;
    qaTurnCount = data.turn_number || (qaTurnCount + 1);
    qaSessionLabel.textContent = `SESSION · 第 ${qaTurnCount} 輪對話`;
    appendBotBubble(data);
  }catch(e){
    document.getElementById("qa-loading-bubble")?.remove();
    const b = document.createElement("div");
    b.className = "bubble bot";
    b.innerHTML = `<div class="ans" style="color:#ff9b9b">查詢失敗：${escHtml(e.message)}。請稍後再試。</div>`;
    qaConvo.appendChild(b);
  }finally{
    qaBusy = false;
    qaSend.disabled = !qaInput.value.trim();
    qaInput.focus();
  }
}
