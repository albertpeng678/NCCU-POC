# 職類擴增 + offcanvas 重構 + 分頁回上一批 — 設計文件（Track B）

> 2026-06-08（Session 8）。三個相關但可獨立實作的子需求：B-1 職涯 50→100、B-2 offcanvas 職類 IA、B-3 分頁「回上一批」。
> B-1+B-2 耦合（職涯清單 + 其分類）；B-3 獨立（純前端分頁）。mockup：`frontend/mockup-offcanvas.html`、`frontend/mockup-pager.html`。

## Goal

(1) 把推薦職涯由 50 擴增到 **100**；(2) 把 offcanvas 職涯目錄從**扁平清單**重構為**職能分類 accordion + 搜尋**（100 項可查找）；(3) 推薦結果分頁補「**回上一批**」。

## Background / 現況

- `frontend/careers.js`（由 `backend/career_skills.json` 生成）：**50 職涯**，扁平。
- offcanvas（`index.html` `#offcanvas`）：title + 搜尋框 + 「全部 50 種職涯」+ 扁平 `#oc-list` listbox。100 項會難找。
- `pagination.js`：pool 全保留（`appendPool` 累積），但用**只進不退的 `shown` 游標**渲染 → 翻過去回不來。
- 推薦結果底部單一「換一批推薦」（`#reroll-btn`）。

---

## B-1：職涯擴增 50 → 100

新增 **50 職涯**（22 必加 ★ + 28 可選 ◯，研究依政大六大強項 + 台灣職市挑選，避開太冷門/無課綱支撐者）。完整 100 依 **8 大職能分類**（同 B-2 IA）：

1. **商業與管理**：產品經理(PM)、管理顧問、創業家、企業策略規劃★、商業分析師(BA)★、客戶成功經理(CSM)★、營運企劃/商業開發(BD)★、業務銷售、電商營運、供應鏈管理、採購/供應商管理◯、財務長(CFO)◯、經營分析◯、醫療管理、觀光旅遊管理
2. **金融與財會**：財務分析師、投資銀行家、會計師、風險管理師、精算師、金融科技、財富管理顧問★、證券分析師★、稅務顧問★、量化交易員(Quant)◯、企業金融(企金)◯、內部稽核◯、銀行儲備幹部(MA)◯
3. **法律、公共與政策**：律師、公務員、公共政策、外交官、都市規劃師、不動產顧問、法務專員★、法律遵循(Compliance)★、地政士/不動產估價師★、國會助理★、國際組織/涉外事務★、智慧財產權/專利◯、都市計畫技師◯、不動產投資/土地開發◯、政治幕僚◯、財稅行政(公職)◯
4. **行銷、傳播與創意**：行銷企劃、數位行銷、品牌管理、廣告創意、媒體購買、公關專員、社群媒體經理、新聞記者、廣播電視主持、影視製作、內容創作者、內容行銷★、編輯/出版★、公共關係顧問◯、危機公關◯、影視/節目企劃◯、文化創意/IP企劃◯
5. **科技、數據與體驗**：軟體工程師、AI工程師、資安工程師、資料科學家、統計分析師、UX/UI設計師、遊戲策劃、用戶體驗研究員(UXR)★、商業智慧(BI)分析師★、數位轉型顧問★、教育科技(EdTech)產品◯、市場研究員◯、系統分析師(SA)◯
6. **國際與外語**：跨文化溝通、翻譯口譯、國際貿易、海外業務★、跨境電商營運★、駐外/外派專員◯、國際會展(MICE)企劃◯
7. **教育、研究與人文**：教師、學術研究員、心理師、社工師、圖書館資訊、企業內訓/人才發展(L&D)★、數位學習設計師★、升學/生涯諮詢顧問◯、博物館/策展◯
8. **永續與社會**：ESG/永續長、NGO工作者、ESG顧問/永續報告★、碳管理/碳盤查◯、社會企業/影響力投資◯、地緣政治/政經風險分析◯

> 合計 100（**使用者 spec review 時確認最終清單**；數量微調以湊整 100）。生技製藥歸科技/數據或商業（醫療生技類項目少，不獨立成類）。

### 後端牽連（B-1 必做）
- `backend/career_skills.json`：每個**新職涯**補 `{label, skills:[...]}`（技能關鍵字，供 fan-out 檢索）。技能關鍵字由 LLM 生成草稿、人工微調（沿用既有 50 筆風格：6–8 個中文技能 + 少量英文）。
- `frontend/careers.js`：由 `career_skills.json` **重生成**（100 筆）；`HOT_PICKS` 6 個維持不變。
- `backend/career_budget`：新 50 職涯**離線補預算**（`scripts/build_career_budget.py` 的 `ONLY_MISSING=1` 冪等增量灌；未補者線上落回即時路徑，不致壞）。⚠️ 注意 embedding 限流，分批灌。
- **新增分類資料**：`career_categories.js`（或 careers.js 內）定義 8 分類 → 職涯陣列 的對應（B-2 用）。

---

## B-2：offcanvas 職類目錄重構

依 RAG/UX 研究方案 A（NN/g 背書）：**單軸「職能」分類 accordion + 搜尋優先**。

### 版面（見 `mockup-offcanvas.html`）
- 頂部：標題「職涯目錄」+ 關閉鈕 + **搜尋框**（純文字 placeholder「搜尋職涯…」，**無 emoji**）。
- **不放熱門 chips**（首頁已有，避免重複）。
- 下方：**8 大職能分類 accordion**，每類標題含名稱 + 數量標；**預設全收合**（或展開第 1 類，spec review 定）。點標題展開/收合；點職涯 → 帶入查詢觸發推薦（同現有 pill 行為）。

### 互動
- **搜尋**：輸入即時過濾，命中項跨類顯示並**自動展開命中分類** + 高亮關鍵字；清空恢復分類視圖。
- 分類軸＝**職能**（非產業/學院別）；每職涯**只歸一主類**（跨域擇一，如金融科技歸金融、UX/UI 歸科技）。

### 前端改動
- `frontend/index.html`：`#offcanvas` 內容由扁平 `#oc-list` 改為「搜尋框 + accordion 容器」。
- `frontend/app.js`：以 `career_categories` 渲染 accordion；搜尋過濾/展開邏輯；點職涯沿用既有 `selectCareer` 流程。
- `frontend/style.css`：accordion / 分類標題 / 數量標 / 展開動畫 / RWD（手機抽屜全寬）。navy glassmorphism 沿用。
- cache-bust `?v=N` bump。

---

## B-3：分頁「回上一批」（純前端、獨立）

pool 全保留 → 回上一批是**索引切換、0 網路請求**。

### 邏輯（`pagination.js`）
- state 由「只進游標 `shown`」改為**批次索引模型**：`{ pool, batchSize, batchIndex, totalBatches }`（`totalBatches = ceil(pool.length / batchSize)`）。
- 新增 `prevBatch(state)`：`batchIndex > 0` 才退；回該批切片。
- `nextBatch` 改以 `batchIndex` 前進；到最後一批 + 池可續 → 沿用既有續池（新 seed 呼叫 `/recommend/stream`）後 `totalBatches` 更新。
- 既有 `appendPool`/`groupBatch` 沿用。**保持純函式、可單元測試。**

### UI（`app.js` + `style.css`，見 `mockup-pager.html`）
- 把單一 `#reroll-btn` 升級為**批次導覽列**（變體 A）：`‹ 上一批　第 N / M 批　換一批 ›` + 下方**小圓點**指示。
- 第 1 批「上一批」**disabled**；翻到底維持現有「看完了」end-state。
- 「上一批」「換一批」都走 `pagination` 純前端切片（換一批池乾才續池）。
- 無 emoji；箭頭用 `‹ ›` 字元。

---

## 架構 / 檔案

| 檔 | 改動 |
|----|------|
| `backend/career_skills.json` | +50 職涯 skills |
| `backend/career_categories.json`（新） | 8 分類 → 職涯對應（單一事實來源） |
| `frontend/careers.js` | 由上重生成（100 筆）|
| `frontend/career-categories.js`（新） | 前端分類資料（或由 careers.js 帶出）|
| `frontend/pagination.js` | 批次索引模型 + `prevBatch` |
| `frontend/index.html` | offcanvas accordion 結構 + 導覽列 |
| `frontend/app.js` | accordion 渲染/搜尋、導覽列 UI/狀態 |
| `frontend/style.css` | accordion / 導覽列 / RWD |
| `scripts/build_career_budget.py` | 跑 `ONLY_MISSING` 補新 50 預算 |

## 測試策略

### 單元（確定性、TDD）
- `pagination.js`：`prevBatch`/`nextBatch`/批次索引/邊界（第 1 批不可退、末批續池）、`totalBatches` 計算。
- 分類資料完整性：100 職涯每個**恰歸一類**、無漏無重複、`careers.js` 與 `career_skills.json` 一致。

### **Playwright MCP e2e + 跨裝置（GO 條件、使用者硬性要求）**
依 playwright skill 規範流程，用 Playwright MCP 跑**真瀏覽器**：
- **桌機 / 平板 / 手機 三裝置**各驗：
  - offcanvas 開合、分類展開/收合、搜尋過濾與自動展開、點職涯觸發推薦。
  - 分頁「換一批 → 上一批 → 回到原批」資料一致、第 N/M 批指示正確、第 1 批 disabled、續池接續。
  - 手機抽屜全寬、無 rubber-band、0 console error。
- **未過不得合併**（與 Q&A 遷移同等驗收紀律）。

---

## 不在本輪
- 推薦/Q&A 模型相關（另案）。File Search 成本優化（已評估＝工具固有、暫不處理）。

## 待 spec review 確認
1. 最終 100 清單（含我補的 4 個湊整：銀行儲備幹部(MA)/系統分析師(SA) 等）增刪。
2. offcanvas 預設「全收合」vs「展開第 1 類」。
3. B-3 是否與 B-1/B-2 同一批實作，或先獨立出貨（B-3 純前端、最快）。
