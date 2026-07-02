# 問答檢索品質改造設計（query rewriting + 多路一次檢索 + rerank）

> 日期：2026-07-02　狀態：**設計中，待使用者審核**
> 問題：問答模式（/qa）問「想成為 PM 要修什麼課」回傳不對題的課（電子商務/銷售/審計數據/作業價值管理），漏掉產品管理/使用者研究核心。

## 1. 根因（三路實測 + 五路 context7 確認）

問答模式缺了推薦模式早有的兩套機制：

1. **沒有 query 拆解**：整句口語原文丟去單次 file_search，「PM」沒展開成能對上課名的關鍵字。`career_skills.json` 裡就有 PM 的 8 個技能，但只有推薦模式讀、問答從不查。
2. **沒有 rerank**：file_search 黑盒順序直接餵生成；`score_threshold=0.0`、`max_num_results=5`。

**實測鐵證**：
- 整句檢索 top-1 是「中國現代散文選讀」(0.776)，真正的「使用者體驗設計」只 0.736 排第 4。
- score 平滑無懸崖（0.66–0.78），無關課分數會贏過相關課 → **門檻切不乾淨、必須靠 rerank**。

## 2. 已否決的方向（附實測理由，避免重蹈）

- **doc enrichment（把技能寫進課程內容）**：實測餘弦——「短課綱+豐富化」0.3821 **輾壓**「長課綱相關課+豐富化」0.2493。短文本比重失衡把沾邊課拉爆、擠掉真相關課，**製造新災難**，放棄。
- **fan-out 多路檢索（每技能各一次 search）**：成本高 + 延遲高，對串流 UX 不可接受（使用者否決）。
- **attributes 存技能陣列**：OpenAI attributes value 只支援 string/number/bool、**無 array**（官方 reference 確認）；逗號字串又無法被 filter 子字串命中。
- **外部商用 rerank API（Cohere/Jina）**：資料送外部、不透明（使用者否決）。

## 3. 方案：query rewriting + 多路一次檢索 + LLM rerank

核心：**用 OpenAI 原生「query 陣列一次檢索」取代 fan-out**（規格內、一次呼叫、上限 5 路），再用 rerank 壓掉沾邊課。全走系所過濾已建好的手動 `vector_stores.search` 路徑（不用黑盒 file_search tool）。

```
使用者問句
  → (多輪) condense 指代消解（既有，保留）
  → ① query rewrite：LLM 把口語問句 → ≤5 個對齊課名語彙的 query 字串（json array）
  → ② 一次 vector_stores.search(query=[...≤5路...], max_num_results=大N如20-30)   ← 官方原生多路、一次呼叫
  → ③ rerank：LLM listwise 對候選池重排（複用推薦模式 stage2 模式）→ 取 top-K
  → ④ 注入 top-K context 生成（既有 retrieve_dept_filtered_context 的注入路徑）
  → citations 用候選 course_id
```

**實測驗證**：`vector_stores.search(query=陣列)` 官方支援、上限 5（400 錯誤實證）；但單靠多路仍撈到「作業價值管理」當第一 → 證明 ③ rerank 為必要第二段。

## 4. 元件

1. **`rewrite_to_queries(question, history?) -> list[str]`**（backend/qa.py 或 dept_query 旁）：LLM structured output，把問句 rewrite 成 ≤5 個聚焦 query 字串。指示「用政大課程可能出現的正式詞彙（如『使用者體驗』非『用戶研究』）」。抽不出 → 回 `[原問句]`（degrade 成單路，不崩）。
2. **多路檢索**：`vector_stores.search(vector_store_id, query=queries, max_num_results=N)`（N≈20-30）。空 → fallback 原問句單路 → 仍空 → 回退既有 file_search 純語意。
3. **`rerank_courses(question, candidates) -> list`**：**LLM-as-reranker**（複用 `OPENAI_MODEL` gpt-5.4-mini，listwise，類似 `stage2_annotate_pool_async`）——對候選池依「與使用者意圖相關性」重排+可選過濾，取 top-K（K≈8-10）。**理由：不本地部署、prompt 透明可審計、零新依賴、複用已驗證模式**（符合使用者「不本地部署 + 透明」）。
4. **qa.py 整合**：把此路做成問答預設檢索路徑（取代 model 自驅 file_search）；系所條件命中時疊加 filter。

## 5. 待使用者確認的決策點

1. **rerank 用 LLM-as-reranker（gpt-5.4-mini）** 為主案（不本地、透明、零依賴）。替代：若堅持用「開源 reranker 模型」，走 HuggingFace Inference API 跑 bge-reranker-v2-m3/gte-multilingual（模型開源，但資料經 HF、非本地）。**兩者都不本地部署**——請選。
2. rewrite 路數上限（規格上限 5，建議用滿 5）。
3. 是否同時把此改造用到推薦模式（本設計預設**只改問答**，YAGNI）。

## 6. 錯誤處理 / 延遲

- rewrite 失敗 → 單路原問句；search 空 → 退純語意；rerank 失敗 → 用 search 原順序。全程 fail-open、不 0 筆。
- 延遲：多 1 次 rewrite LLM + 1 次 rerank LLM（rewrite 輸出短、rerank listwise 一次），比 fan-out（6-8 次 embedding）輕；實測問答延遲基線 ~11-35s，可接受。

## 7. 測試

- 單元：rewrite 輸出 ≤5 且非空、fallback；rerank 排序穩定、fail-open。
- **e2e（真後端+真瀏覽器，5x consecutive）**：問「想成為 PM 要修什麼課」→ 表格出現產品/UX/專案管理相關課、**不再是散文選讀/作業價值管理**；問其他職涯（記者/資料分析師）亦對題；模糊查詢不回歸。

## 8. 非目標（YAGNI）

- 不改推薦模式、不做 doc enrichment、不 fan-out、不引入本地 GPU 服務、不引入外部商用 rerank API。
