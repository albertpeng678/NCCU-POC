"""Sentry 觀測性輔助（純函式，不 import sentry_sdk，便於單測與輕量）。

設計：把預期內的「暫時性 Gemini 錯誤」（429 限流 / 503 過載 / 月度 spending cap）在 before_send
降成 warning + 穩定 fingerprint（同類歸成一個 issue、不再被當嚴重 bug 洗版/Escalating），
但**降級不丟棄**（仍記錄、看得到次數趨勢）。真 bug（其他 4xx/5xx、一般例外）一律不動、維持原樣。

另提供 traces_sampler：超長串流端點（/recommend/stream ~2m、/qa/stream ~42s）是「正常但久」，
降取樣避免污染 performance 報表與燒 quota。
"""
from google.genai import errors as genai_errors

# 預期內暫時性錯誤 → (fingerprint, tag) 對映
_FP_SPENDING = ("gemini-spending-cap", "spending-cap")
_FP_RATE = ("gemini-rate-limit-429", "rate-limit")
_FP_DEMAND = ("gemini-high-demand-503", "high-demand")


def classify_and_downgrade(event, hint):
    """Sentry before_send：暫時性 Gemini 錯誤 → warning + fingerprint；其餘原樣回傳（不丟棄）。"""
    exc_info = (hint or {}).get("exc_info")
    exc = exc_info[1] if exc_info else None
    if not isinstance(exc, genai_errors.APIError):
        return event  # 非 Gemini API 錯誤（含真 bug）一律不動

    code = getattr(exc, "code", None)
    msg = (getattr(exc, "message", "") or "").lower()

    # ⚠️ spending cap 無專屬 code，只能靠 Google 的 message 文案比對；若 Google 改字/在地化，
    # 會退回純 429(rate-limit) 分類 —— 仍是 warning、不會漏報，只是 fingerprint 較不精確。
    if "spending limit" in msg or "spending cap" in msg:
        fp, tag = _FP_SPENDING          # spending cap 文案優先於純 429
    elif code == 429:
        fp, tag = _FP_RATE
    elif code == 503:
        fp, tag = _FP_DEMAND
    else:
        return event                    # 其他 APIError（400/404 等真 bug）維持 error 醒目

    event["level"] = "warning"
    event["fingerprint"] = [fp]
    event.setdefault("tags", {})["gemini_transient"] = tag
    return event


# before_send 入口（與 classify_and_downgrade 同義，給 sentry_sdk.init 用）
before_send = classify_and_downgrade


STREAM_SAMPLE_RATE = 0.1


def stream_traces_sampler(sampling_context):
    """traces_sampler：路徑含 /stream（/recommend/stream、/qa/stream）→ 0.1，其餘 → 1.0。

    雙來源穩健讀取：transaction name 與 asgi_scope.path 任一含 /stream 即判定。
    """
    ctx = sampling_context or {}
    name = (ctx.get("transaction_context") or {}).get("name", "") or ""
    path = ""
    scope = ctx.get("asgi_scope")
    if scope is not None:
        try:
            path = scope.get("path", "") or ""
        except Exception:
            path = ""
    if "/stream" in name or "/stream" in path:
        return STREAM_SAMPLE_RATE
    return 1.0
