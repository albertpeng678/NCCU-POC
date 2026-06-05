# 手機 UX 修正設計：rubber-band 防彈跳 + Q&A 模式隱藏 hamburger

> 設計日期：2026-06-05（Session 6）。範圍：純前端、~4 行改動。Karpathy：外科手術式最小 diff。

## 一、問題

1. **手機 rubber-band（橡皮筋）**：頁面在手機上會「到處亂滑、亂動」（overscroll 彈跳）。Q&A 模式下 `.hero` 是內捲容器（`overflow-y:auto`）但未設 `overscroll-behavior` → scroll-chaining 到 document → 整頁彈跳；推薦模式則是 document 本身彈跳。
2. **Q&A 模式的 hamburger 突兀**：左上 hamburger（`#menu-btn`）開的是「職涯目錄 offcanvas」，**推薦模式專屬**；`setMode()` 切到 qa 時未隱藏它 → 在問答模式按了開出無關面板，顯得突兀（NN/G：hamburger 應留給合情境的導航，否則成死路）。

## 二、研究依據

- **rubber-band**：`overscroll-behavior:none` 擋 scroll-chaining + 彈跳，覆蓋 Chrome/Android/Safari 16+（2026 覆蓋率高）。參考站 aistockmap 用更重的 app-shell（`html{overflow:hidden}`），但需重構捲動結構、風險高 → **本案採輕量 `overscroll-behavior`**，僅在實機（舊 iOS）仍彈時才升級 app-shell（未來選項）。
- **hamburger**：Playwright 三裝置 mockup 確認「問答隱藏 hamburger」後左上只剩 logo、乾淨無死導航；推薦模式 hamburger 照常。

## 三、設計（純前端）

### #1 rubber-band（`frontend/style.css`，~3 行）
為捲動容器加 `overscroll-behavior:none`：
- `html, body`：推薦模式 document 捲動。
- `body.qa-active .hero`：問答模式內捲容器。

（不動現有 `height:100dvh`/flex 佈局；只加防彈跳，不碰辛苦調好的捲動結構。）

### #2 隱藏 hamburger（`frontend/app.js` 的 `setMode`，~1-2 行）
- `setMode(mode)` 內加：`menuBtn.hidden = (mode === 'qa')`（問答隱藏、推薦顯示）。
- 切到問答時若 offcanvas 已開 → 順手 `closeOffcanvas()`，避免殘留開啟狀態。
- offcanvas 與其邏輯保留（推薦模式照用），僅隱藏觸發鈕。

## 四、測試（Playwright MCP 跨裝置 e2e）

手機(390)/平板(768)/桌機(1280) 三尺寸驗證：
1. **hamburger**：問答模式 `#menu-btn` 不可見（`hidden`）；切回推薦可見。
2. **overscroll**：捲動容器的 computed `overscroll-behavior` === `none`（`html`/`body`/`.hero`）。
3. **無回歸**：三尺寸下版面正常、`body.qa-active` 捲動正常、console 0 error；before/after 截圖。

## 五、範圍邊界（不做）

- app-shell 重構（`html{overflow:hidden}`）——僅當輕量版在實機仍彈跳時的未來升級。
- hamburger 改全域用途（選項 B）/ 其他模式功能。
- 任何與此兩點無關的版面調整。

## 六、成功標準

1. 手機實測（或 Playwright 模擬）問答模式不再整頁彈跳。
2. 問答模式無 hamburger、推薦模式有；切換正常。
3. 三裝置版面無回歸、console 無 error。
