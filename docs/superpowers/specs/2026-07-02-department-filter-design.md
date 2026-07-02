# 系所過濾（Department Filter）設計文件

> 日期：2026-07-02（Session 12）
> 狀態：**設計中，待使用者審核**
> 問題來源：使用者問「推薦給我 10 門歷史系的課」，問答回傳全是外系課（法律系、英文系、韓文、中文系），無一門歷史系。

---

## 1. 問題與根因（已用四路獨立調查 + 正式 store 實測證實）

**症狀**：使用者在問答介面指定某系所（如「歷史系」），檢索結果全是外系課。

**根因（兩層疊加）**：

1. **store 沒有「系所」這個可過濾的欄位**：建庫時每個檔案只掛了 `course_id` 與 `syllabus_url` 兩個 attribute，**沒有 `department`**。系所只以純文字寫在課綱內文，只能影響語意相似度、無法硬過濾。檢索路徑（`backend/qa.py`）的 `file_search` tool 也**沒帶任何 `filters`**、`score_threshold=0.0`。
2. **模型自驅 file_search 會發散**：正式問答讓模型在 Responses API 內自行決定檢索 query，把「歷史系」擴散成發散語意查詢。實測鐵證：
   - 後端直接用乾淨 query `vector_stores.search("歷史系")` → 前 10 命中 **7-8 門真歷史系**。
   - 正式 Responses API file_search（模型自驅）→ 20 筆候選中 **0 門歷史系**（甚至撈到「全方位理財」「服務學習」）。

**資料髒污（放大問題、也是修復難點）**：`department` 欄位全庫 **535 種**不重複原始字串，同一系散成多種寫法（中文系 → `中文一甲`/`中文三乙`/`中文碩一中文博一中文碩二中文博二`/`中文系`…），研究所值把班別串接（`歷史碩一歷史博一歷史碩二歷史博二`），且無統一 canonical 名。course_id 前綴不可當系所鍵（`000`/`041`/`042`/`070` 等為跨系共用的通識/共同科前綴）。

## 2. 目標與成功標準

- 使用者指定某系所時，**只回傳該系的課**（歷史系查詢 → 全歷史系）。
- 使用者指定學制時（「歷史系碩士的課」「我只要大學部的課」）→ 只回該學制。**學士／碩士／博士分開**；學制別名要對照（大學部/大學/本科→學士、研究所→碩士+博士、碩士班→碩士…）。學制可單獨指定（不綁系所，如「只要大學部」）。
- 使用者問題模糊/未指定系所時 → 行為不變（純語意檢索）。
- **不得**因系所抽取失敗或對照缺漏而誤過濾成 0 筆（要有 fallback）。
- 驗證門檻：真瀏覽器 e2e「問歷史系 → 回傳全歷史系」，非只單元測試。

## 3. 主流做法依據（context7 + web 調研）

- **建庫端正規化**：controlled vocabulary（canonical 系所清單）當護欄 + LLM 一次性離線批次映射（enum 強制只能填清單內值）→ 固化成 committed 靜態對照表；canonical 拆成數個正交扁平 facet（`dept_canonical` + `college` + `degree_level`），存進 vector store attribute。
- **查詢端**：self-querying / query-construction（LangChain SelfQueryRetriever、LlamaIndex auto-retrieval）——用 LLM 把問句拆成「乾淨語意 query」＋「結構化 filter」；抽出的系名用**同義字表**或 embedding 最近鄰正規化成 canonical 精確值（LangChain high-cardinality benchmark：post-LLM embedding 修正 83% 最佳）；由**後端**用乾淨 query + `eq`/`in` filter 呼叫 `vector_stores.search`（預設 `rewrite_query=false`，不讓模型發散）；抽不到/對不上就不帶 filter，退回純語意。
- **OpenAI attributes 限制**：最多 16 keys、key ≤64 字、value ≤512 字（string/number/bool）。可事後用 `POST /vector_stores/{vs}/files/{file_id}` **只更新 attributes、不重傳檔案**（免重新 embedding）。

## 4. 架構：AI 收斂 + 後端受控檢索

> 核心分工（使用者強調，見 memory `qa-condense-plus-controlled-retrieval`）：**AI 收斂垃圾 query 是必要設計、絕不能拿掉**；問題不在「AI 碰 query」，而在「收斂完把檢索整包丟給模型自由發揮 + 無系所硬篩」。

| 步驟 | 誰做 | 內容 |
|---|---|---|
| 1. 收斂 + 抽條件 | **AI（保留現有 condense，擴充）** | 把模糊問句收斂成乾淨檢索意圖，並抽出結構條件 `{department?, degree_level?}` |
| 2. 正規化 | **後端（三層）** | 把抽到的系名/學制對照到 canonical：①AI 已限定只輸出清單值 ②不在清單→模糊/語意最近鄰 snap（rapidfuzz 字面 + embedding）③別名表快路；三層都對不上→視為未指定 |
| 3. 受控檢索 | **後端（改這裡）** | 用乾淨 query 呼叫 `vector_stores.search`，帶 `filters`（系所/學制硬篩）。無條件→不帶 filter |
| 4. 生成 | **AI** | 從後端撈回的候選課寫成表格答案 |

**Fallback 準則**：步驟 1 抽不到系所、或步驟 2 對不上任何 canonical → 不帶 filter，退回現有純語意檢索（避免 0 筆）。

## 5. 資料模型（新增 attribute）

每個 vector store 檔案新增（在既有 `course_id`、`syllabus_url` 之外）：

| attribute key | 型別 | 範例值 | 用途 |
|---|---|---|---|
| `dept_canonical` | string | `歷史學系` | 系所硬過濾主鍵 |
| `college` | string | `文學院` | 學院粗粒度過濾（如「文學院的課」；同主題跨系分辨，例 Python 課資管/資科/統計重點不同） |
| `degree_level` | string | `學士` / `碩士` / `博士` / `碩博` / `通識` / `其他` | 學制硬過濾 |

- **碩博合開課**（`歷史碩一歷史博一…`）→ `degree_level="碩博"`；查「碩士」用 `in ["碩士","碩博"]`、查「博士」用 `in ["博士","碩博"]`、查「學士」用 `eq "學士"`、查「研究所」用 `in ["碩士","博士","碩博"]`。
- `college` 值來自官方「學院→系所」名冊（元件 2）；每個 `dept_canonical` 對應唯一 `college`。跨院通識/體育/外文中心等歸「共同/通識」類。
- 學制別名對照（查詢端）：大學部/大學/本科/學士班→`學士`；研究所→`碩士`+`博士`；碩士班→`碩士`；博士班→`博士`。

## 6. 元件（各自獨立、可單獨測試）

1. **`dept_mapping` 產生器（離線一次性）** — 輸入 535 個 distinct 髒值 + canonical 系所清單，LLM enum-constrained 批次映射，輸出 `backend/dept_mapping.json`（`{dirty: {dept_canonical, college, degree_level, confidence}}`）。含 regex 前處理去雜訊。
2. **canonical 系所/學院清單** — **✅ 已完成**，見 `2026-07-02-nccu-official-dept-vocab.md`：web search 政大官方名冊（各學院官網），12 學院 + 特殊單位，提供 `dept_canonical`/`college` 值域，當 controlled vocabulary（護欄）。對照 535 髒值 → 186 詞幹，**184 詞幹（99.9%）已對應官方**；6 歧義詞幹（`資博產`跨商/資訊院、`國關`、`國教碩`、`原碩專`、`生科學程`、`數位`跨院）列清單待 PM 人工核。
3. **store attribute backfill 腳本** — 讀 `dept_mapping.json` + `courses_meta.json`，對既有 2718 檔 `POST files/{id}` 補 `dept_canonical`/`college`/`degree_level`（**不重建 store、不重 embedding**）。
4. **`dept_extractor`（查詢端）** — 從收斂後 query 抽 `{department?, college?, degree_level?}`（LLM structured output，**prompt/schema 內嵌 canonical 清單 enum，讓 LLM 直接輸出標準名**，吃掉多數變體/簡稱/錯字如「文院/文苑」→文學院）。
5. **`canonical_matcher`（查詢端正規化，三層）** — ①LLM 已 enum 限定 ②若仍不在清單 → **模糊/語意最近鄰**（rapidfuzz 字面編輯距離治錯字 + embedding 語意最近鄰治換句話說；LangChain high-cardinality benchmark：post-LLM embedding 修正 83% 最佳）③別名表快路（文院→文學院）。**信心不足/三層皆不中 → 回 None（不過濾）**。regex **不用於此**，僅用於建庫端前處理。
6. **filter 組裝** — 由抽取結果組 `filters`（eq/in/and，含 college/degree 組合、碩博 `in` 展開）；空條件回 None。
7. **檢索路徑改造** — 系所查詢走「後端 `vector_stores.search` + filter」；無條件維持現狀。整合進 `qa.py`。

## 7. 錯誤處理

- 抽取失敗 / LLM 例外 → 當作未指定系所，純語意。
- 對照表無對應 canonical → 不過濾（記 log 供回填）。
- filter 後 0 筆 → 放寬（去掉 degree_level 只留 dept；仍 0 → 去 filter 純語意），並在答案誠實說明。
- 新課出現對照表沒有的髒值 → 標 `其他` + 告警，append-only 回填。

## 8. 測試（TDD，先寫失敗測試）

- **單元**：`dept_mapping` 產生器（給髒值→期望 canonical）；`dept_extractor`（問句→期望 slot；含亂打→None）；filter 組裝（slot→期望 filter dict / None）；碩博 `in` 展開。
- **資料驗收**：對照表覆蓋率統計（%已映射）、桶大小分布異常偵測、低信心項清單。
- **整合 / e2e（真後端 + 真瀏覽器）**：問「歷史系的課」→ 全歷史系；問「歷史系碩士」→ 只碩士；問模糊題→行為不變、不 0 筆；亂打系名→退回純語意不崩。**5x consecutive 0 flake 才算綠**。

## 9. 執行方法論（使用者要求）

- **TDD skill**：每個元件 red → green → commit。
- **dispatch parallel agents**：建庫端（元件 1-3）與查詢端（元件 4-7）不共享狀態，平行派遣；並行上限 3-5，任一 return 立即補下一個。
- **code review skill**：實作完對 diff 跑五面向 review。
- **全然獨立的 auditor agent（merge 前關卡）**：另開不繼承實作脈絡的 agent，對抗性稽核——(a) 抽查 `dept_mapping.json` 映射正確性（尤其碩博/通識/低信心/桶異常）、(b) 獨立重跑 e2e 確認全歷史系、(c) 查 regression。與 code reviewer 分開。

## 10. 已定案決定（使用者確認 2026-07-02）

1. **學制可過濾（確認）**：使用者可只要大學部/學士、或碩士/博士，篩得出來（`degree_level` facet + 別名對照）。「歷史系」未指定學制時 → 回各學制、答案依學士/碩士/博士**分組呈現**。
2. **通識課** → `degree_level="通識"`，「歷史系的課」預設**不含**通識。
3. **canonical 清單來源 → web search 政大官方名冊**（非 AI 亂猜）。
4. **college facet → 要做**（同主題跨系分辨，例 Python 課各系重點不同）。

## 11. 非目標（YAGNI）

- 推薦模式（`POST /recommend`，職涯導向、不吃系所輸入）本次不改。
- 年級（一/二/三/四）細粒度過濾本次不做。
- store 重建（改用只補 attribute 的省錢做法）。
