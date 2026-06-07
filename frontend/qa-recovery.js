// qa-recovery.js — 問答失敗復原的純邏輯（無 DOM 依賴，可單測）。
//  - qaErrorUiState(error_type): 暫時性過載→可重試 / 查無資料→引導換問法
//  - buildRetryState(question): 重試泡泡的回填值與按鈕文案
//  - noMatchChips(followups): 查無資料時顯示的方向 chips（有 followup 用之，否則預設）

export const TRANSIENT_MSG = "伺服器正忙，剛剛這題沒能完成。稍等一下再試一次通常就好了。";
export const NO_MATCH_MSG = "這個問題我在課程資料裡查不到對應內容。試試換個問法，或看看下面方向。";

const _DEFAULT_CHIPS = ["想培養的能力", "想了解的領域", "適合的職涯方向"];

export function qaErrorUiState(errorType) {
  if (errorType === "no_match") return { kind: "no_match", retryable: false };
  // rate_limited / timeout / unknown / 任意值 → 暫時性，鼓勵重試
  return { kind: "transient", retryable: true };
}

export function buildRetryState(question) {
  return {
    refillValue: question || "",
    buttonLabel: "重新提問",
    disabledLabel: "重試中…",
  };
}

export function noMatchChips(followups) {
  return (Array.isArray(followups) && followups.length) ? followups : _DEFAULT_CHIPS.slice();
}
