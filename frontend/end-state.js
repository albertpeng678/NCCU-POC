// end-state.js — 推薦「翻到底」的「看完了」畫面（純函式組 HTML，無 DOM 依賴，可單測）。
// 設計決定（user 2026-06-07）：翻到底不再即時叫 AI；改給柴犬「都叼來了」+ 三個下一步。

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

export function buildEndStateHtml({ career } = {}) {
  const c = esc(career);
  const forCareer = c ? `「${c}」` : "這個方向";
  return (
    `<div class="end-state">` +
      `<p class="end-title">柴犬把${forCareer}能找到的好課都叼來了！</p>` +
      `<p class="end-sub">你已經看完所有相關課程。想繼續探索的話：</p>` +
      `<div class="end-actions">` +
        `<button class="end-btn" type="button" data-action="new-career">換個職涯方向</button>` +
        `<button class="end-btn" type="button" data-action="to-qa">用問答聊聊想培養的能力</button>` +
        `<button class="end-btn ghost" type="button" data-action="rewatch">從頭再看一次</button>` +
      `</div>` +
    `</div>`
  );
}
