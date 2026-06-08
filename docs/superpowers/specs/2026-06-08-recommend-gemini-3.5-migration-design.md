# 職涯推薦 pipeline 遷移 Gemini 3.5-flash — 設計文件（Track C）

> 2026-06-08（Session 8）。承 Q&A 已遷 3.5；本輪把**推薦 pipeline**（fan-out + stage2 + derive）也遷 3.5。
> 動機：避開 2.5-flash high-demand 503、與 Q&A 一致、原生 structured output；且 `career_budget` 灌庫用此 pipeline → 遷後灌庫跑 3.5。

## Goal

把 `backend/recommend.py` 的生成模型從 `gemini-2.5-flash` 遷到 **`gemini-3.5-flash`**（thinking_budget→thinking_level 正確對應、fan-out 明設「只檢索一次」、保留 2.5 env fallback），**並把成本主宰的 fan-out 從「每技能一支（6-8 支 file_search）」合併為「單一支帶全部技能的檢索」**，把每次推薦/建庫的檢索成本砍 ~6-8x（NT$5-7 → ~NT$1）。

## Background / 現況

`backend/recommend.py` 用單一常數 `_GEN_MODEL = "gemini-2.5-flash"`。兩條 production 關鍵呼叫（`build_recommendation_instrumented_async` 與 `career_budget` 灌庫共用）：
- **fan-out**（`fanout_query_skill_async` ×6-8 並行）：`file_search(top_k=8)`、**無 response_schema**、**預設 thinking** → `extract_json_array` 解析自由文字 JSON。
- **stage2**（`stage2_annotate_pool_async`）：`response_schema=_RankedOutput` + `thinking_budget=0`、**無 file_search**。
- 另：`derive_skills_for_career_async`（清單外職涯）：json + `thinking_budget=0`、無 file_search。

問題：2.5-flash 正經歷 high-demand 503（CLAUDE.md #16/#17），灌新 50 budget 卡關；Q&A 已在 3.5 順跑。

## 研究結論（context7 + web + spike 實測，功課）

1. **`thinking_budget` 已被 `thinking_level` 取代**（Gemini 3.x：minimal/low/medium/high，**預設 medium**）。budget 仍向後相容、但官方建議改 level、且**不可同時用兩者**。
2. **⚠️ 鐵則：只換模型字串、不動 thinking → 預設變 medium → 靜默降質/增延遲/增成本**。故**每個呼叫都必須明設 thinking_level**。
3. **3.5 結構化輸出可與 file_search + thinking 並用**（Q&A 已驗）；fan-out 因此**可改 response_schema**（省 extract_json_array）——但屬可選清理（方案 B），本輪先不做。
4. **spike 實測**（3.5 fan-out + file_search + `thinking_level=low`）：成功的那支 16s、`extract_json_array` 正常 parse、`tool_use≈8306`、~NT$0.92/支 → **3.5 fan-out 可行**。
5. **成本主宰＝fan-out 的 file_search `tool_use`**（不透明、~8k/支 × 6-8 ≈ NT$5-7/次）；budget 命中時線上免付（同 2.5 的取捨）。
6. **503 現為 Gemini 全模型暫時性過載**（2.5、3.5 皆中）→ 完整驗證與 budget 灌庫須等過載退去。

## 架構

### `backend/recommend.py`

- **模型 + fallback**：`_GEN_MODEL = os.environ.get("REC_MODEL", "gemini-3.5-flash")`（同 Q&A 的 `QA_MODE` 精神；出問題可 `REC_MODEL=gemini-2.5-flash` 即時切回，免改碼重部署）。
- **thinking 對應（model-aware helper）**：新增純函式
  ```
  def _thinking(kind: str) -> types.ThinkingConfig:
      # kind: "off"（分類/排序/推導，無 file_search）| "grounded"（fan-out，有 file_search）
      if _GEN_MODEL.startswith("gemini-3"):
          return types.ThinkingConfig(thinking_level="minimal" if kind == "off" else "low")
      return types.ThinkingConfig(thinking_budget=0) if kind == "off" else None  # 2.5：off→0；grounded→預設
  ```
  套用：
  - `stage2_annotate_pool_async`、`derive_skills_for_career(_async)`、`stage2_group`：`thinking_config=_thinking("off")`（3.5→minimal，符合 #10「此分類+排序任務關 thinking 不掉品質」）。
  - `fanout_query_skill_async`、`stage1_retrieve(_async)`：`thinking_config=_thinking("grounded")`（3.5→low，保 grounding；實測可行）。
- **fan-out 只檢索一次**：`fanout_query_skill_async` 的 prompt 末尾加一句：
  > 「你只有 1 次 File Search 檢索預算——檢索一次取得相關課綱後直接回，不要反覆多次檢索。」
  （action-budget pattern；封住 agentic 多輪重複計費的上界。注意：內建 file_search 無官方硬上限，此為 prompt 層強導引、非 100% 保證；spike 顯示聚焦查詢本就單輪。）
- fan-out 仍用 `extract_json_array`（最小變更）；budget 格式不變。

### 成本優化：fan-out 合併（核心改動）

把「每技能一支 file_search（`fanout_query_skill_async` ×6-8 並行）」合併為**單一支帶全部技能的檢索**：
- 新增 `consolidated_retrieve_async(client, store, career, skills)`：**一支 file_search**，prompt 帶**全部技能**、要 ~`POOL_SIZE` 門課、`top_k` 提高（新常數 `CONSOLIDATED_TOP_K`，初值 ~24，補足合併後的召回廣度）、加「只檢索一次」指令；`extract_json_array` 解析 → 候選池。
- `build_recommendation_instrumented_async` / `stream_recommendation` 的 stage1 改呼叫 `consolidated_retrieve_async`（取代 `fanout_retrieve_async`）。
- **保留 `fanout_retrieve_async` 不刪**，用 env `REC_RETRIEVE=consolidated`(預設) / `fanout`(回退) 切換 → 供 judge A/B 對照、品質不如預期可即時切回。
- 成本：1 支 ~8k tool_use（vs 6-8 支）→ 檢索成本砍 ~6-8x。延遲：少了並行的尾端等待，單支可能略慢於最慢的一支、但總體相當或更好。
- **品質把關（必做）**：judge A/B —— 同幾個職涯，比 consolidated vs fanout 的 `judge.py` 推薦理由分數；consolidated 不低於 fanout（或差距在容忍內）才採預設。掉太多 → 維持 fanout（REC_RETRIEVE）。

### 明確不做（排除）

- **清單外職涯「隨用隨快取」**：會把使用者亂打的奇怪/無效職涯一堆 upsert 進 `career_budget` 污染資料 → **不做**。清單外維持每次即時（量少、可接受）。

### `scripts/build_career_budget.py`
- 不改碼（用既有 pipeline）。遷移後它自動跑 3.5。

## 資料流
不變（fan-out → 合併池 → stage2 標註 → build_ranked_courses）；僅模型與 thinking 參數變。

## 錯誤處理
- 沿用 main.py client 的 429/503 退避重試（`HttpOptions timeout`）。
- `REC_MODEL` fallback：3.5 若異常可切 2.5。
- fan-out 單支 503/失敗 → 既有 `return_exceptions` 容錯（回 []，不拖垮整體）。

## 測試策略

### 單元（確定性）
- `_thinking("off")`/`_thinking("grounded")` 對 3.x 回 minimal/low、對 2.x 回 budget=0/None（不打 Gemini，純函式）。
- 各 generate_content config 帶正確 thinking_config（mock client，斷言 config）。
- fan-out prompt 含「只有 1 次 File Search 檢索預算」字串。
- `REC_MODEL` env 切換 `_GEN_MODEL`。

### Live 驗證（等 Gemini 503 退去）
- spike 重跑全 fan-out（6-8 支）+ stage2：量 grounding 不空、parse 正常、延遲、總成本。
- **judge 品質不低於基線**（`judge.py` 對推薦理由）：抽數個職涯比對 **(a) 3.5 vs 2.5**、**(b) consolidated vs fanout** 的 judge 分數；consolidated(3.5) 不低於 fanout(2.5) 基線才採預設，否則用 REC_MODEL/REC_RETRIEVE 回退。
- **Playwright e2e**：真前端推一個職涯 → 結果正常渲染、三區課程、理由、citation/課綱連結、0 console error（桌機代表即可，recommend 非跨裝置敏感如 offcanvas，但仍跑一次）。

### 部署後
- `railway ssh` 在容器內跑 `ONLY_MISSING=1 python scripts/build_career_budget.py`（3.5、內網 DB）灌新 50 budget（等 503 退去）。抽驗新職涯秒回。

## 影響範圍 / 文件
- CLAUDE.md #10：`thinking_budget=0` 改述為 3.5 的 `thinking_level=minimal`（off 任務）；fan-out grounded=low。
- CLAUDE.md #12：推薦也 3.5（與 Q&A 一致）；`REC_MODEL` env 可切回 2.5。

## 不在本輪
- fan-out 改 response_schema（方案 B 清理，免 extract_json_array）——後續。
- 把 fan-out 從 6-8 支併成單支廣檢索（成本大降但犧牲多樣性）——另議。
- Q&A（已遷）。

## 風險與緩解
- **3.5 預設 medium 靜默降質**：強制每呼叫明設 thinking_level（鐵則）。
- **fan-out 成本（×6-8 × ~8k tool_use）**：budget 快取吸收線上成本；「只檢索一次」封住多輪暴衝上界。
- **503 全域過載**：REC_MODEL 可切 2.5；validation/budget 等過載退去；fan-out 單支容錯不拖垮。
- **品質回歸**：judge 比對 3.5 vs 2.5 把關；不過則維持 2.5（REC_MODEL）。

## 已拍板決策（spec review 定案）
1. stage2/derive → `thinking_level="minimal"`；fan-out/consolidated → `low`。
2. 最小遷移保留 `extract_json_array`（不順手改 response_schema；方案 B 留後續）。
3. **fan-out 合併為單一支檢索**（成本根本解，砍 ~6-8x）+ judge A/B 把關 + REC_RETRIEVE 可回退。
4. **不做**清單外隨用隨快取（污染風險）。
