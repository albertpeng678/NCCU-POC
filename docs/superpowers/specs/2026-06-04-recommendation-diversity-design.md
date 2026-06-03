# 推薦多樣性設計（Recommendation Diversity）

> 日期：2026-06-04
> 狀態：設計定案，待實作
> 相關檔案：`backend/recommend.py`、`backend/models.py`、`backend/main.py`、`frontend/app.js`、`frontend/index.html`、`frontend/style.css`

## 一句話

讓「同一個職涯重複查詢」每次回傳**不同但仍相關**的課程組合，把全校 2877 門課的價值釋放出來，並在 UI 加入「換一批」與「導向問答」的引導。

---

## 一、問題

目前推薦流程是**確定性**的：

1. `stage1_retrieve`：Gemini File Search 依「職涯 + 固定技能關鍵字」取**前 15 門**最相關課程。
2. `stage2_group`：把候選分成 core/supporting/extended，輸出約 10 門。

因為職涯關鍵字固定、每門課的嵌入向量固定，**相關度排序每次都一樣** → 同職涯永遠推同一批課。下載的 2877 門課中，大多數**永遠沒機會出現**。

## 二、目標與非目標

**目標**
- 同職涯重複查詢 → 不同的 10 門課組合（**跨次數輪替**）。
- 抽換出來的課**仍然相關**（不離題）。
- 最小改動、疊加在現有流程上，不破壞既有 `/recommend` 行為與測試。
- 可重現（固定種子 → 固定結果），方便測試與除錯。

**非目標（本次不做）**
- 「有記憶」的歷史去重（保證逐步走完全部 2877 課）→ 列為 **Phase 2**，未來用 `query_log` 實作。
- 單次回應內的跨系所多元（intra-list diversity）→ 本次聚焦跨次輪替。

---

## 三、設計 A：後端多樣性（擴大候選池 + 定錨 + 隨機抽樣）

### 比喻
抽獎箱：不要只放 10 張固定籤；放進 **40 張「都相關」的籤**，每次從中抽一批 → 每次組合不同，但都相關。

### 流程改動

```
stage1_retrieve(取 top 40，原為 15)
        │  candidates: list[{course_id, course_name, relevance}]
        ▼
sample_candidates(candidates, seed)   ← 新增
        │  保留前 ANCHOR 門「定錨」+ 從其餘隨機抽至 SAMPLE_SIZE 門
        ▼
stage2_group(分組為 core/supporting/extended，輸出 ~10 門)   ← 不變
```

### 新增函式 `sample_candidates`

```python
def sample_candidates(
    candidates: list[dict],
    seed: int,
    anchor_count: int = ANCHOR_COUNT,
    sample_size: int = SAMPLE_SIZE,
) -> list[dict]:
    """從候選池選出送往 stage2 的子集。

    規則：
    - 前 anchor_count 門（最相關）一律保留 → 確保品質定錨。
    - 其餘候選用 random.Random(seed) 洗牌後，補滿到 sample_size 門。
    - 候選數 <= sample_size 時，原樣回傳（不抽樣）。
    - 給定相同 seed → 結果完全相同（可重現）。
    """
```

### 參數預設值（皆為模組常數，易調整）

| 常數 | 預設 | 說明 |
|---|---|---|
| `POOL_SIZE` | 40 | stage1 檢索目標數量（prompt 中的「找 N 門」） |
| `ANCHOR_COUNT` | 4 | 每次必留的最相關門數（保品質） |
| `SAMPLE_SIZE` | 18 | 送進 stage2 的候選數（stage2 再縮成 ~10） |

### 介面：`seed` 從哪來

- `RecommendRequest` 新增**選用**欄位 `seed: int | None`。
- **未帶 seed**（正式使用/前端預設）：後端以亂數產生 seed → 每次呼叫都不同 → 達成輪替。
- **帶 seed**（測試/重現）：使用指定 seed → 結果固定。
- `RecommendResponse` 回傳本次實際使用的 `seed`（除錯/可重現用；前端可忽略）。

### 既有相容性
- `stage2_group`、`join_metadata`、`deduplicate_by_prefix` 全部不變。
- dry-run 5 課小 store：候選數 < `SAMPLE_SIZE` → 自動跳過抽樣，行為等同現狀（測試不破）。

---

## 四、設計 B：UI 引導

### B1：「換一批推薦 🔄」按鈕
- 位置：推薦結果區下方。
- 行為：以**相同職涯**重新呼叫 `/recommend`（不帶 seed → 後端新亂數）→ 渲染新一批 10 門課。
- 載入時沿用既有 loading 狀態；失敗沿用既有 503 處理。

### B2：底部 CTA「導向問答」
- 位置：推薦結果最底部，一句話 + 連結樣式。
- 文案（暫定）：「想深入比較這些課？切到**問答模式**問我 →」。
- 行為：點擊 → 切換到問答模式（呼叫既有 mode 切換邏輯），游標移至問答輸入框。**不帶入特定課程、不修改課程卡。**

### 不做
- ❌ 課程卡內嵌「問問這門課」按鈕（已駁回）。

---

## 五、測試策略（TDD）

**單元測試 `sample_candidates`（純函式，不需 Gemini）**
- 相同 seed → 輸出完全相同（可重現）。
- 不同 seed → 輸出不同（在候選數足夠時）。
- 前 `anchor_count` 門必定包含於結果。
- 候選數 <= `sample_size` → 原樣回傳、不漏不重。
- 結果無重複 course_id、長度 == `min(sample_size, len(candidates))`。

**既有測試**
- `tests/backend/test_recommend.py`、`test_api.py` 全數仍須通過（介面向後相容）。

**Live 驗證（dry-run / full store）**
- 同職涯、兩個不同 seed → 課程集合不同但皆相關。
- full store 後再驗一次（dry-run 課太少看不出輪替效果）。

**前端**
- Playwright E2E：點「換一批」後課程卡內容改變；點底部 CTA 切到問答模式。

---

## 六、實作順序（給 writing-plans 的提示）

1. 後端：`POOL_SIZE`/`ANCHOR_COUNT`/`SAMPLE_SIZE` 常數 + `sample_candidates`（先 TDD）。
2. 後端：`stage1_retrieve` prompt 改 40；`build_recommendation*` 串接 `sample_candidates` + seed。
3. 後端：`models.py` 加 `seed`（request 選用 / response 回傳）；`main.py` 產生亂數 seed。
4. 前端：B1 換一批按鈕 + B2 底部 CTA + 樣式。
5. 驗證：單元測試、live 同職涯雙 seed、Playwright E2E。

## 七、未來（Phase 2，不在本次）
- 用 `query_log` 記錄各職涯已推 course_id，下次排除/降權 → 保證逐步覆蓋全 2877 課，並處理「走完重置」。
