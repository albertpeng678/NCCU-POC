# Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Prerequisite:** Plan 2 (Backend) must be complete and running. Backend URL must be known.
>
> **MANDATORY before Task 2:** Invoke `superpowers:brainstorming` sub-flow using `frontend-design` skill for visual design decisions (layout, component hierarchy, interaction states). Do NOT implement until design is approved.
>
> **E2E Testing:** Uses Playwright MCP tools for cross-device and cross-viewport validation.

**Goal:** Build a static HTML/CSS/JS SPA with autocomplete career selector, 6 hot-pick pills, and grouped recommendation cards — deployed to Railway Static Site.

**Architecture:** Single-page app. `app.js` calls `POST /recommend` on backend. Results rendered as three grouped card sections. Playwright MCP validates E2E on 4 viewport sizes.

**Tech Stack:** HTML5, CSS3 (no framework), Vanilla JS (ES2022), Playwright MCP (E2E), Railway Static Site

**Design Constraints (from brainstorming):** Navy blue primary, ≤3 colors total, no emoji, NNgroup single-click pills, grouped card results with syllabus link.

---

## File Map

| File | Responsibility |
|------|---------------|
| `frontend/index.html` | Page structure, semantic HTML |
| `frontend/style.css` | Navy blue theme, card layout, autocomplete dropdown, responsive |
| `frontend/app.js` | State, autocomplete logic, API call, result rendering |
| `frontend/careers.js` | Career list (50 items) for autocomplete — generated from `career_skills.json` |

---

## Task 1: Invoke frontend-design Skill (MANDATORY)

> This task MUST complete before any HTML/CSS implementation.

- [ ] **Step 1: Invoke `superpowers:brainstorming` → frontend-design flow**

The frontend-design skill will determine:
- Exact color palette (navy blue + up to 2 accent colors)
- Typography scale
- Card layout (grid vs. stack)
- Autocomplete dropdown behavior
- Loading/error states
- Responsive breakpoints

Record the approved design decisions before proceeding to Task 2.

---

## Task 2: HTML Structure

**Files:**
- Create: `frontend/index.html`

- [ ] **Step 1: Create `frontend/index.html`**

(Implement based on approved frontend-design output. The structure below is a starting scaffold — adjust per design approval.)

```html
<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>政大課程推薦 — 找到最適合你的課程</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header class="site-header">
    <div class="container">
      <h1 class="site-title">政大課程推薦</h1>
      <p class="site-subtitle">輸入職涯目標，發現最適合的課程組合</p>
    </div>
  </header>

  <main class="container">
    <section class="search-section" aria-label="職涯搜尋">
      <div class="search-box">
        <label for="career-input" class="search-label">你想往哪個方向發展？</label>
        <div class="autocomplete-wrapper">
          <input
            id="career-input"
            type="text"
            class="career-input"
            placeholder="輸入或選擇職涯目標..."
            autocomplete="off"
            aria-autocomplete="list"
            aria-controls="autocomplete-list"
            aria-expanded="false"
          >
          <ul id="autocomplete-list" class="autocomplete-list" role="listbox" hidden></ul>
        </div>
        <button id="search-btn" class="search-btn" type="button" disabled>
          查詢課程推薦
        </button>
      </div>

      <div class="pills-section">
        <span class="pills-label">熱門選擇：</span>
        <div class="pills-container" id="hot-picks" role="group" aria-label="熱門職涯選擇">
          <!-- Rendered by app.js -->
        </div>
      </div>
    </section>

    <section class="results-section" id="results-section" aria-live="polite" hidden>
      <div class="results-header">
        <h2 class="results-title" id="results-title"></h2>
      </div>

      <div class="groups-container">
        <div class="group-block" id="group-core">
          <h3 class="group-title">核心技能</h3>
          <p class="group-description">直接對應職涯核心能力</p>
          <div class="cards-grid" id="cards-core"></div>
        </div>
        <div class="group-block" id="group-supporting">
          <h3 class="group-title">輔助技能</h3>
          <p class="group-description">強化周邊能力、增加競爭力</p>
          <div class="cards-grid" id="cards-supporting"></div>
        </div>
        <div class="group-block" id="group-extended">
          <h3 class="group-title">延伸視野</h3>
          <p class="group-description">跨域拓展、差異化視角</p>
          <div class="cards-grid" id="cards-extended"></div>
        </div>
      </div>
    </section>

    <section class="loading-section" id="loading-section" hidden aria-label="載入中">
      <div class="loading-spinner" aria-hidden="true"></div>
      <p class="loading-text">分析課程中，請稍候...</p>
    </section>

    <section class="error-section" id="error-section" hidden role="alert">
      <p class="error-text" id="error-message"></p>
    </section>
  </main>

  <footer class="site-footer">
    <div class="container">
      <p>資料來源：政大全校課程查詢系統 114學年度第2學期</p>
    </div>
  </footer>

  <script src="careers.js"></script>
  <script src="app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Validate HTML structure in browser**

Open `frontend/index.html` directly in browser (file://). Confirm:
- Page renders without JS errors
- All sections present in DOM
- `aria-*` attributes correct

- [ ] **Step 3: Commit**

```bash
git add frontend/index.html
git commit -m "feat(frontend): semantic HTML structure with accessibility attributes"
```

---

## Task 3: CSS — Navy Blue Theme

**Files:**
- Create: `frontend/style.css`

(Implement based on approved frontend-design output. The variables below define the design token layer — adjust per approval.)

- [ ] **Step 1: Create `frontend/style.css`**

Key design tokens to lock in (values from frontend-design approval):
```css
:root {
  /* Color system — max 3 colors */
  --color-primary: #1a2a4a;      /* navy blue */
  --color-accent: #2563eb;       /* mid blue (interactive) */
  --color-surface: #f8faff;      /* near-white background */

  /* Typography */
  --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;

  /* Spacing */
  --space-sm: 8px;
  --space-md: 16px;
  --space-lg: 24px;
  --space-xl: 40px;

  /* Borders */
  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 16px;
}

/* Reset */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: var(--font-sans); background: var(--color-surface); color: var(--color-primary); line-height: 1.6; }

/* Container */
.container { max-width: 900px; margin: 0 auto; padding: 0 var(--space-md); }

/* Header */
.site-header { background: var(--color-primary); color: white; padding: var(--space-xl) 0; }
.site-title { font-size: 1.75rem; font-weight: 700; letter-spacing: -0.02em; }
.site-subtitle { font-size: 1rem; opacity: 0.75; margin-top: var(--space-sm); }

/* Search */
.search-section { padding: var(--space-xl) 0 var(--space-lg); }
.search-label { display: block; font-size: 1.1rem; font-weight: 600; margin-bottom: var(--space-md); }
.autocomplete-wrapper { position: relative; }
.career-input {
  width: 100%; padding: 14px var(--space-md); font-size: 1rem;
  border: 2px solid #d0d9e8; border-radius: var(--radius-md);
  outline: none; transition: border-color 0.2s;
}
.career-input:focus { border-color: var(--color-accent); }
.autocomplete-list {
  position: absolute; top: calc(100% + 4px); left: 0; right: 0;
  background: white; border: 1px solid #d0d9e8; border-radius: var(--radius-md);
  box-shadow: 0 4px 16px rgba(26,42,74,0.12); z-index: 100; max-height: 280px; overflow-y: auto;
}
.autocomplete-item {
  padding: 10px var(--space-md); cursor: pointer; font-size: 0.95rem;
  transition: background 0.1s;
}
.autocomplete-item:hover, .autocomplete-item[aria-selected="true"] {
  background: #eef3ff; color: var(--color-accent);
}

/* Search button */
.search-btn {
  width: 100%; margin-top: var(--space-md); padding: 14px;
  background: var(--color-accent); color: white; border: none;
  border-radius: var(--radius-md); font-size: 1rem; font-weight: 600;
  cursor: pointer; transition: opacity 0.2s;
}
.search-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.search-btn:not(:disabled):hover { opacity: 0.88; }

/* Pills */
.pills-section { margin-top: var(--space-lg); display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-sm); }
.pills-label { font-size: 0.875rem; color: #556; flex-shrink: 0; }
.pills-container { display: flex; flex-wrap: wrap; gap: var(--space-sm); }
.career-pill {
  padding: 6px 14px; background: white; border: 1.5px solid #c8d6ea;
  border-radius: 20px; font-size: 0.875rem; cursor: pointer;
  transition: all 0.15s; color: var(--color-primary);
}
.career-pill:hover { border-color: var(--color-accent); color: var(--color-accent); background: #eef3ff; }

/* Groups */
.groups-container { display: flex; flex-direction: column; gap: var(--space-xl); padding-bottom: var(--space-xl); }
.group-block { }
.group-title { font-size: 1.15rem; font-weight: 700; color: var(--color-primary); margin-bottom: 4px; }
.group-description { font-size: 0.85rem; color: #667; margin-bottom: var(--space-md); }
.cards-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: var(--space-md); }

/* Cards */
.course-card {
  background: white; border: 1.5px solid #dde6f5; border-radius: var(--radius-lg);
  padding: var(--space-md) var(--space-lg); display: flex; flex-direction: column; gap: var(--space-sm);
}
.card-name { font-size: 1rem; font-weight: 700; color: var(--color-primary); }
.card-meta { font-size: 0.8rem; color: #667; }
.card-reason { font-size: 0.875rem; color: #334; line-height: 1.5; flex: 1; }
.card-link {
  display: inline-block; margin-top: var(--space-sm); padding: 6px 14px;
  background: var(--color-primary); color: white; border-radius: var(--radius-sm);
  font-size: 0.8rem; font-weight: 600; text-decoration: none; align-self: flex-start;
  transition: opacity 0.15s;
}
.card-link:hover { opacity: 0.82; }

/* Results header */
.results-section { padding-top: var(--space-xl); }
.results-header { margin-bottom: var(--space-xl); }
.results-title { font-size: 1.4rem; font-weight: 700; }

/* Loading */
.loading-section { text-align: center; padding: var(--space-xl) 0; }
.loading-spinner {
  width: 40px; height: 40px; border: 3px solid #d0d9e8;
  border-top-color: var(--color-accent); border-radius: 50%;
  margin: 0 auto var(--space-md); animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
.loading-text { color: #667; }

/* Error */
.error-section { padding: var(--space-lg); background: #fff3f3; border: 1px solid #fcc; border-radius: var(--radius-md); margin-top: var(--space-lg); }
.error-text { color: #c00; }

/* Footer */
.site-footer { border-top: 1px solid #dde6f5; padding: var(--space-lg) 0; text-align: center; font-size: 0.8rem; color: #89a; }

/* Responsive */
@media (max-width: 640px) {
  .site-title { font-size: 1.4rem; }
  .cards-grid { grid-template-columns: 1fr; }
  .pills-section { flex-direction: column; align-items: flex-start; }
}
```

- [ ] **Step 2: Verify CSS in browser (4 viewports)**

Open `frontend/index.html` in Chrome. Use DevTools to check:
- Desktop 1280px: search box + pills visible, no overflow
- Tablet 768px: layout adjusts cleanly
- Mobile 375px: single column, readable

- [ ] **Step 3: Commit**

```bash
git add frontend/style.css
git commit -m "feat(frontend): navy blue CSS theme with responsive grid and card layout"
```

---

## Task 4: `careers.js` + `app.js` — Logic

**Files:**
- Create: `frontend/careers.js`
- Create: `frontend/app.js`

- [ ] **Step 1: Create `frontend/careers.js`**

```javascript
// frontend/careers.js
// Generated from backend/career_skills.json — 50 careers
const CAREERS = [
  "產品經理(PM)", "資料科學家", "軟體工程師", "UX/UI設計師", "行銷企劃",
  "品牌管理", "財務分析師", "投資銀行家", "會計師", "公關專員",
  "管理顧問", "創業家", "廣告創意", "新聞記者", "社群媒體經理",
  "人力資源", "律師", "外交官", "公務員", "NGO工作者",
  "教師", "心理師", "社工師", "學術研究員", "業務銷售",
  "供應鏈管理", "電商營運", "遊戲策劃", "內容創作者", "風險管理師",
  "精算師", "不動產顧問", "ESG/永續長", "資安工程師", "AI工程師",
  "金融科技", "影視製作", "公共政策", "都市規劃師", "醫療管理",
  "國際貿易", "跨文化溝通", "翻譯口譯", "圖書館資訊", "廣播電視主持",
  "媒體購買", "數位行銷", "統計分析師", "生技製藥", "觀光旅遊管理"
];

const HOT_PICKS = [
  "產品經理(PM)", "資料科學家", "軟體工程師",
  "管理顧問", "行銷企劃", "財務分析師"
];
```

- [ ] **Step 2: Create `frontend/app.js`**

```javascript
// frontend/app.js
const CONFIG = {
  API_URL: "https://nccu-backend.up.railway.app"  // UPDATE before deploy
};

// --- DOM refs ---
const input       = document.getElementById("career-input");
const dropdown    = document.getElementById("autocomplete-list");
const searchBtn   = document.getElementById("search-btn");
const hotPicks    = document.getElementById("hot-picks");
const resultsSection = document.getElementById("results-section");
const loadingSection = document.getElementById("loading-section");
const errorSection   = document.getElementById("error-section");
const errorMsg       = document.getElementById("error-message");
const resultsTitle   = document.getElementById("results-title");

// --- State ---
let selectedCareer = null;
let activeIdx = -1;

// --- Autocomplete ---
function filterCareers(query) {
  if (!query.trim()) return [];
  const q = query.toLowerCase();
  return CAREERS.filter(c => c.toLowerCase().includes(q)).slice(0, 8);
}

function renderDropdown(items) {
  dropdown.innerHTML = "";
  if (!items.length) { dropdown.hidden = true; return; }
  items.forEach((item, idx) => {
    const li = document.createElement("li");
    li.className = "autocomplete-item";
    li.setAttribute("role", "option");
    li.setAttribute("id", `opt-${idx}`);
    li.textContent = item;
    li.addEventListener("mousedown", () => selectCareer(item));
    dropdown.appendChild(li);
  });
  dropdown.hidden = false;
  input.setAttribute("aria-expanded", "true");
  input.setAttribute("aria-activedescendant", "");
  activeIdx = -1;
}

function selectCareer(career) {
  selectedCareer = career;
  input.value = career;
  dropdown.hidden = true;
  input.setAttribute("aria-expanded", "false");
  searchBtn.disabled = false;
}

function clearSelection() {
  selectedCareer = null;
  searchBtn.disabled = true;
}

input.addEventListener("input", () => {
  clearSelection();
  const items = filterCareers(input.value);
  renderDropdown(items);
});

input.addEventListener("keydown", (e) => {
  const items = dropdown.querySelectorAll(".autocomplete-item");
  if (!items.length) return;
  if (e.key === "ArrowDown") {
    e.preventDefault();
    activeIdx = Math.min(activeIdx + 1, items.length - 1);
    items.forEach((el, i) => el.setAttribute("aria-selected", i === activeIdx ? "true" : "false"));
    input.setAttribute("aria-activedescendant", `opt-${activeIdx}`);
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    activeIdx = Math.max(activeIdx - 1, 0);
    items.forEach((el, i) => el.setAttribute("aria-selected", i === activeIdx ? "true" : "false"));
  } else if (e.key === "Enter" && activeIdx >= 0) {
    e.preventDefault();
    selectCareer(items[activeIdx].textContent);
  } else if (e.key === "Escape") {
    dropdown.hidden = true;
  }
});

document.addEventListener("click", (e) => {
  if (!e.target.closest(".autocomplete-wrapper")) {
    dropdown.hidden = true;
  }
});

// --- Hot picks pills ---
HOT_PICKS.forEach(career => {
  const btn = document.createElement("button");
  btn.className = "career-pill";
  btn.textContent = career;
  btn.type = "button";
  btn.setAttribute("aria-label", `選擇職涯：${career}`);
  btn.addEventListener("click", () => selectCareer(career));
  hotPicks.appendChild(btn);
});

// --- Recommend ---
function showLoading() {
  resultsSection.hidden = true;
  errorSection.hidden = true;
  loadingSection.hidden = false;
}

function showError(msg) {
  loadingSection.hidden = true;
  resultsSection.hidden = true;
  errorSection.hidden = false;
  errorMsg.textContent = msg;
}

function renderCard(course) {
  const card = document.createElement("div");
  card.className = "course-card";
  card.innerHTML = `
    <div class="card-name">${escHtml(course.name)}</div>
    <div class="card-meta">${escHtml(course.department)} · ${escHtml(course.teacher)} · ${course.credits} 學分</div>
    <div class="card-reason">${escHtml(course.reason)}</div>
    <a class="card-link" href="${escHtml(course.syllabus_url)}" target="_blank" rel="noopener noreferrer">查看課綱</a>
  `;
  return card;
}

function escHtml(str) {
  return String(str).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function renderResults(data) {
  loadingSection.hidden = true;
  errorSection.hidden = true;

  resultsTitle.textContent = `${data.career} — 推薦課程`;

  const groups = { core: "cards-core", supporting: "cards-supporting", extended: "cards-extended" };
  Object.entries(groups).forEach(([key, id]) => {
    const container = document.getElementById(id);
    container.innerHTML = "";
    (data.groups[key] || []).forEach(course => container.appendChild(renderCard(course)));
  });

  resultsSection.hidden = false;
  resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function fetchRecommendation(career) {
  showLoading();
  try {
    const resp = await fetch(`${CONFIG.API_URL}/recommend`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ career }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    const data = await resp.json();
    renderResults(data);
  } catch (e) {
    showError(`查詢失敗：${e.message}。請稍後再試。`);
  }
}

searchBtn.addEventListener("click", () => {
  if (selectedCareer) fetchRecommendation(selectedCareer);
});

input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && selectedCareer) fetchRecommendation(selectedCareer);
});
```

- [ ] **Step 3: Open in browser and verify functionality**

Open `frontend/index.html` in Chrome (note: CORS will block API calls from file:// — use a local server):

```bash
cd frontend && python -m http.server 3000
```

Navigate to `http://localhost:3000`. Verify:
1. Type "PM" → dropdown shows "產品經理(PM)"
2. Click pill "資料科學家" → fills input, enables button
3. Arrow keys navigate dropdown
4. Escape closes dropdown

- [ ] **Step 4: Commit**

```bash
git add frontend/careers.js frontend/app.js
git commit -m "feat(frontend): autocomplete, career pills, result rendering"
```

---

## Task 5: E2E Tests with Playwright MCP

> Uses Playwright MCP tools available in the Claude Code session.
> Tests run against live frontend (local server) + live backend.
>
> **Cross-device viewports:** mobile 375px, tablet 768px, desktop 1280px, wide 1440px

- [ ] **Step 1: Start local servers**

Terminal 1 (backend):
```bash
cd backend && uvicorn main:app --port 8000
```

Terminal 2 (frontend):
```bash
cd frontend && python -m http.server 3000
```

- [ ] **Step 2: Navigate to frontend and take baseline snapshot**

Use Playwright MCP:
```
browser_navigate to http://localhost:3000
browser_snapshot → verify: search input visible, 6 pills rendered, results hidden
```

Expected snapshot includes:
- `career-input` input element
- 6 `.career-pill` buttons (産品経理PM, 資料科學家, 軟體工程師, 管理顧問, 行銷企劃, 財務分析師)
- `results-section` with `hidden` attribute

- [ ] **Step 3: Test autocomplete interaction**

```
browser_type into #career-input: "PM"
browser_snapshot → verify: autocomplete-list visible, "產品經理(PM)" item present
browser_click on autocomplete item "產品經理(PM)"
browser_snapshot → verify: input value = "產品經理(PM)", search-btn enabled (not disabled)
```

- [ ] **Step 4: Test pill single-click**

```
browser_navigate to http://localhost:3000
browser_click on pill "資料科學家"
browser_snapshot → verify: input value = "資料科學家", search-btn not disabled
```

- [ ] **Step 5: Test full recommendation flow (E2E)**

```
browser_click on search-btn
browser_wait_for: results-section to become visible (timeout 30s)
browser_snapshot → verify:
  - results-title contains "資料科學家"
  - #group-core visible with at least 1 .course-card
  - #group-supporting visible
  - #group-extended visible
  - Each card has .card-link with href containing "newdoc.nccu.edu.tw"
```

- [ ] **Step 6: Test "查看課綱" link**

```
browser_snapshot → find first .card-link href
browser_navigate to that href
browser_snapshot → verify: page title contains "政大教學大綱" or course name
browser_navigate_back
```

- [ ] **Step 7: Mobile viewport test (375×812)**

```
browser_resize to width=375, height=812
browser_navigate to http://localhost:3000
browser_snapshot → verify:
  - No horizontal overflow
  - Search input full-width
  - Pills wrap to multiple lines or scroll
  - Font sizes readable

browser_click on pill "軟體工程師"
browser_click on search-btn
browser_wait_for: results-section visible
browser_snapshot → verify: cards in single column, card-link visible and tappable
browser_take_screenshot → save as e2e/screenshots/mobile-375.png
```

- [ ] **Step 8: Tablet viewport test (768×1024)**

```
browser_resize to width=768, height=1024
browser_navigate to http://localhost:3000
browser_snapshot → verify: layout adapts, cards in 2-column grid if width allows
browser_take_screenshot → save as e2e/screenshots/tablet-768.png
```

- [ ] **Step 9: Desktop viewport test (1280×800)**

```
browser_resize to width=1280, height=800
browser_navigate to http://localhost:3000
browser_snapshot → verify: full layout, pills in one row
browser_take_screenshot → save as e2e/screenshots/desktop-1280.png
```

- [ ] **Step 10: Wide viewport test (1440×900)**

```
browser_resize to width=1440, height=900
browser_navigate to http://localhost:3000
browser_snapshot → verify: max-width container constraint respected, no content stretches full-width
browser_take_screenshot → save as e2e/screenshots/wide-1440.png
```

- [ ] **Step 11: Error state test**

```
# Temporarily set CONFIG.API_URL to a non-existent URL in app.js (for this test only)
browser_navigate to http://localhost:3000
browser_click on pill "產品經理(PM)"
browser_click on search-btn
browser_wait_for: error-section visible
browser_snapshot → verify: error-section visible, error message present, results hidden
# Restore CONFIG.API_URL
```

- [ ] **Step 12: Keyboard navigation test (accessibility)**

```
browser_navigate to http://localhost:3000
browser_press_key Tab → focus moves to career-input
browser_type "資" into career-input
browser_press_key ArrowDown → first item highlighted
browser_press_key ArrowDown → second item highlighted
browser_press_key Enter → second item selected
browser_snapshot → verify: input filled with selected career
```

- [ ] **Step 13: Commit E2E screenshots**

```bash
mkdir -p e2e/screenshots
git add e2e/screenshots/
git commit -m "test(e2e): Playwright cross-device viewport validation screenshots"
```

---

## Task 6: Deploy to Railway

- [ ] **Step 1: Update `frontend/app.js` CONFIG.API_URL**

Set to actual Railway backend URL (from Plan 2 deployment).

- [ ] **Step 2: Create `.gitignore` entry for Railway**

Verify `railway.toml` covers frontend static site:

```toml
# Add to railway.toml:
[frontend]
root = "frontend"
startCommand = ""
```

- [ ] **Step 3: Final verification with `superpowers:verification-before-completion`**

```bash
# All E2E tests pass (re-run Steps 2-12)
# backend API responds correctly
# All 4 viewport screenshots look correct
```

- [ ] **Step 4: Use `superpowers:finishing-a-development-branch`**

Invoke the finishing skill to determine next steps (merge, PR, or cleanup).

- [ ] **Step 5: Push to GitHub**

```bash
git remote add origin git@github.com:albertpeng678/NCCU-POC.git
GIT_SSH_COMMAND="ssh -i ~/.ssh/github-personal" git push -u origin main
```

Expected: Push succeeds. Verify on github.com/albertpeng678/NCCU-POC.

- [ ] **Step 6: Final commit**

```bash
git add frontend/app.js
git commit -m "feat(frontend): complete SPA with E2E validated across 4 viewports"
```
