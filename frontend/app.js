// app.js — NCCU Course Map frontend logic
const CONFIG = {
  // Local dev default; overwrite before Railway deploy.
  API_URL: (location.hostname === "localhost" || location.hostname === "127.0.0.1")
    ? "http://localhost:8000"
    : "https://nccu-backend.up.railway.app",
};

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
    else if(selectedCareer) fetchRecommendation(selectedCareer);
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
  resCareer.textContent = data.career;
  resLatency.textContent = data.latency_ms ? `GENERATED ${(data.latency_ms/1000).toFixed(1)}s` : "";
  groupsEl.innerHTML = "";
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
    renderResults(await resp.json());
  }catch(e){
    showError(`查詢失敗：${e.message}。請稍後再試。`);
  }
}

searchBtn.addEventListener("click", ()=>{ if(selectedCareer) fetchRecommendation(selectedCareer); });
