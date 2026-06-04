// progressive-md.js — 漸進 markdown 渲染器的 buffer + 節流邏輯（無 DOM/marked 依賴，可單測）。
// 用法（瀏覽器）：
//   const pr = createProgressiveRenderer({ render: (buf)=> el.innerHTML = renderSafeMarkdown(buf) });
//   pr.push(tokenText)  // 每 token；rAF 節流，整段重渲染 → 表格/粗體/標題隨完成即現
//   pr.finish(cb)       // 串流結束：最終權威渲染 + 回呼
// render/schedule/cancel 可注入（測試用同步 scheduler）。

const _raf = (typeof requestAnimationFrame !== "undefined")
  ? (fn) => requestAnimationFrame(fn)
  : (fn) => setTimeout(fn, 16);
const _caf = (typeof cancelAnimationFrame !== "undefined")
  ? (id) => cancelAnimationFrame(id)
  : (id) => clearTimeout(id);

export function createProgressiveRenderer({ render, schedule = _raf, cancel = _caf } = {}) {
  let buf = "";
  let token = null;   // 待處理的排程 handle（null = 無待處理）
  let done = false;

  function flush() {
    token = null;
    render(buf);
  }

  return {
    push(text) {
      if (done || !text) return;
      buf += text;
      if (token === null) token = schedule(flush);   // 節流：一個 frame 內多 token 只排一次
    },
    finish(cb) {
      done = true;
      if (token !== null) { cancel(token); token = null; }
      render(buf);                 // 最終渲染（呼叫端的 renderFinal 會再以權威 data.answer 覆寫）
      if (cb) cb(buf);
    },
    getText() { return buf; },
    abort() {
      done = true;
      if (token !== null) { cancel(token); token = null; }
    },
  };
}
