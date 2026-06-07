// shiba-progress.js — 柴犬等候動畫的純函式核心（無 DOM 依賴，可單測）。
//  - stageNarration(key): 後端真實階段 → 柴犬視角旁白 {now, tip}
//  - easeApproach({elapsedMs}): 「已嗅過 N 本課綱」數字，slow-to-fast 逼近但封頂於 SHIBA_HOLD，
//    **永遠到不了 SHIBA_TOTAL**（杜絕「數字跑完還在等」的尷尬）。
//  - fillToDone(): 唯一補滿入口——只在後端真正 done 時呼叫，補到 SHIBA_TOTAL。
//  - stageIndexFromEvent(d): SSE 事件 → 階段 index，對齊既有 (d.n|0)-1。

export const SHIBA_TOTAL = 2718;   // 全校課綱總數
export const SHIBA_HOLD = 2480;    // 數字逼近上限（~91%），done 前永遠停在這之下

// 5 個真實後端階段（understand/retrieve/filter/compose/finalize）→ 柴犬旁白
const STAGE_NARRATION = {
  understand: { now: "嗅嗅看哪些課對味…", tip: "柴犬鑽進 2,718 本課綱裡打滾，幫你聞出對的味道…" },
  retrieve:   { now: "叼回一疊可能的課本…", tip: "叼了一本課本過來，先放右邊這堆…" },
  filter:     { now: "叼回一疊可能的課本…", tip: "抖抖毛，把不對味的先挑掉…" },
  compose:    { now: "歪頭幫每堂課想理由…", tip: "歪頭思考：這堂適合你嗎？汪汪…" },
  finalize:   { now: "甩尾把課分成三堆…", tip: "尾巴搖得越快，代表找到越多好課！" },
};
const STAGE_FALLBACK = { now: "柴犬正在幫你找課…", tip: "" };

export function stageNarration(key) {
  return STAGE_NARRATION[key] || STAGE_FALLBACK;
}

// Weibull 逼近，時間尺度 τ≈55s 對齊「推薦約 80–135s」的真實等待：
// 整段都在緩緩往上爬，接近尾段才逼近 holdCount，再硬封頂（done 前永遠 < total）。
// τ 太小(如 8s)會在 ~30s 就到頂、剩餘等待數字凍住——故對齊實測等待長度。
export function easeApproach({ elapsedMs = 0, total = SHIBA_TOTAL, holdCount = SHIBA_HOLD } = {}) {
  const t = Math.max(0, elapsedMs) / 1000;
  const frac = 1 - Math.exp(-Math.pow(t / 55, 1.15));
  return Math.min(holdCount, Math.round(holdCount * frac));
}

// done-driven：唯一能讓數字回到滿值的入口（呼叫端只在收到後端 result/done 時呼叫）。
export function fillToDone(total = SHIBA_TOTAL) {
  return total;
}

export function stageIndexFromEvent(d) {
  return (((d && d.n) | 0) - 1);
}
