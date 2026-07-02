# 問答檢索品質改造設計（通用版：query rewrite + 多路一次檢索 + LLM rerank）

> 日期：2026-07-02　狀態：**設計中，待使用者審核**
> 目標：**通用**提升問答檢索對題度——不限職涯，涵蓋職涯（「想成為 PM」）、系所口語縮寫（「傳碩十堂課」）、模糊需求。

## 1. 根因（systematic-debugging Phase 1，多案例實測）

問答檢索不對題，是**同一個病：query understanding 覆蓋不足**，具體三層：

**A. 沒有 query 拆解/rewrite**（職涯案例）
「想成為 PM」整句原文丟去單次檢索，「PM」沒展開成對得上課名的關鍵字。實測：整句檢索 top-1「中國現代散文選讀」(0.776)，真正的「使用者體驗設計」只 0.736。

**B. 沒有 rerank**
file_search 黑盒順序直接餵生成。實測 score 平滑無懸崖（0.66–0.78），無關課分數會贏過相關課 → 門檻切不乾淨、**必須 rerank**。

**C. attribute 硬篩脆弱、不通用**（系所縮寫案例）
系所過濾依賴「LLM extract_slots 抽到系所 + normalize 精確對上 canonical」。實測：
- 「傳碩十堂課」→ `extract_slots` 回**全 None** → filter=null → **過濾蒸發、退回純語意** → 混進資碩工、創新創造力研究中心、大學部。
- 「傳院十堂課」→ 剛好抽到 college=傳播學院 → filter 生效、結果對。
- `normalize("傳碩")=None`（別名表/rapidfuzz/拼音都對不上）。
一旦口語縮寫沒精確命中，整個過濾就消失 → **這就是「payload 篩選效果極差」的真相**。把別名表越補越大是無底洞，不通用。

## 2. 修法方向（使用者定案）

**主力改用「rewrite + rerank」通用檢索；attribute 硬篩退為輔助訊號（非唯一防線）。**

```
使用者問句（任意口語：職涯/系所縮寫/模糊需求）
  → (多輪) condense 指代消解（既有，保留）
  → ① LLM query rewrite（通用）：把問句展開成 ≤5 個「對齊政大課名語彙」的 query 字串（json array）
       ‧職涯「PM」→ ["產品管理 產品經理","使用者體驗設計","數據分析",...]
       ‧系所縮寫「傳碩」→ ["傳播碩士學位學程","閱聽 傳播理論","質性研究方法","傳播 數位媒體"]
       ‧同時（可選）抽出明確系所/學制當「輔助 filter」
  → ② 一次 vector_stores.search(query=[...≤5路...], max_num_results=N≈20-30, filters=輔助filter or None)
       ← OpenAI 原生多路、一次呼叫（規格上限 5 路，實測確認）；不 fan-out、不多付檢索
  → ③ LLM rerank（listwise，複用推薦 stage2 模式，用 OPENAI_MODEL）對候選依「與使用者真實意圖相關性」重排+過濾 → top-K（K≈8-10）
  → ④ 注入 top-K context 生成（既有 retrieve_dept_filtered_context 注入路徑）；citations 用候選 course_id
```

**為什麼這通用**：不靠脆弱的精確 normalize；「傳碩」靠 rewrite 理解（LLM 知道傳碩=傳播碩士）+ rerank 收斂雜訊。職涯、系所縮寫、模糊需求都吃同一條路。attribute 硬篩只在 rewrite 能明確、可靠抽出系所/學制時，當**輔助**縮小候選（命中就加分、沒命中不影響主檢索），不再是唯一防線。

## 3. rerank 決策（使用者選 A）

**LLM-as-reranker（複用 `OPENAI_MODEL` gpt-5.4-mini，listwise）**。理由：不本地部署、prompt 透明可審計、零新依賴/零新服務、複用推薦模式 `stage2_annotate_pool_async` 已驗證的重排。（否決：外部商用 rerank API 資料外流；本地開源模型需 CPU/GPU 部署，使用者不要本地部署。）

## 4. 已否決方向（附實測理由）

- **doc enrichment（技能寫進課程內容）**：實測餘弦「短課綱+豐富化」0.3821 輾壓「長課綱相關課+豐富化」0.2493——短文本比重失衡把沾邊課拉爆、擠掉真相關課，製造新災難。
- **fan-out（每技能各一次 search）**：成本高+延遲高，串流 UX 不可接受。用「query=array 一次多路」取代。
- **attributes 存技能陣列**：官方 value 只 string/number/bool、無 array。
- **把 attribute 硬篩當主力**：實測「傳碩」證明脆弱不通用（本案改為輔助）。

## 5. 元件

1. **`rewrite_to_queries(question, history?) -> {queries: list[str], dept?: str, college?: str, degree?: str}`**：LLM structured output。輸出 ≤5 個聚焦 query（指示用政大正式課名語彙，如「使用者體驗」非「用戶研究」；系所縮寫展開如「傳碩」→傳播碩士相關）+ 可選抽出的系所/學制（供輔助 filter）。抽不出 queries → 回 `[原問句]`（degrade 單路、不崩）。
2. **多路檢索**：`vector_stores.search(query=queries, max_num_results=N, filters=build_dept_filter(輔助slots) or None)`。空→去 filter 重搜→仍空→退既有 file_search 純語意。
3. **`rerank_courses(question, candidates) -> list`**：LLM listwise 重排+過濾取 top-K。fail → 用 search 原順序。
4. **qa.py 整合**：此路做成問答**預設**檢索路徑（取代 model 自驅 file_search）。既有 `retrieve_dept_filtered_context` 併入/改寫成此通用路徑，attribute filter 變成其中的輔助參數。

## 6. 錯誤處理 / 延遲

- 全程 fail-open：rewrite 失敗→單路原句；filter 0 筆→去 filter；rerank 失敗→原順序。絕不 0 筆。
- 延遲：多 1 次 rewrite + 1 次 rerank LLM（皆單次、輸出短/listwise），比 fan-out（6-8 次 embedding）輕。基線問答 ~11-35s，可接受。

## 7. 測試

- 單元：rewrite ≤5 且非空、fallback；filter 輔助組裝；rerank 排序穩定、fail-open。
- **e2e（真後端+真瀏覽器，5x consecutive、通用多案例）**：
  - 「想成為 PM 要修什麼課」→ 產品/UX/專案管理相關，非散文/會計。
  - 「傳碩十堂課」→ 傳播碩士相關，**不混資碩工/創新中心/大學部**。
  - 「想做資料分析」「推薦輕鬆的通識」等模糊/其他類→對題。
  - 純寒暄/離題→不回歸、不 0 筆。

## 8. 非目標（YAGNI）

- 不改推薦模式（career fan-out 保留現狀）、不做 doc enrichment、不 fan-out、不本地部署模型、不引入外部商用 rerank API、不擴充系所別名表當主力。
