"""Sentry 觀測性輔助（純函式，不 import sentry_sdk，便於單測與輕量）。

設計：把預期內的「暫時性 OpenAI 錯誤」（429 限流 / 5xx 過載/高需求）在 before_send
降成 warning + 穩定 fingerprint（同類歸成一個 issue、不再被當嚴重 bug 洗版/Escalating），
但**降級不丟棄**（仍記錄、看得到次數趨勢）。真 bug（其他 4xx、一般例外）一律不動、維持原樣。

另提供 traces_sampler：超長串流端點（/recommend/stream ~2m、/qa/stream ~42s）是「正常但久」，
降取樣避免污染 performance 報表與燒 quota。
"""
import openai

# 預期內暫時性錯誤 → (fingerprint, tag) 對映
_FP_RATE = ("openai-rate-limit-429", "rate-limit")
_FP_TRANSIENT = ("openai-transient-5xx", "high-demand")

# OpenAI 高需求/過載類暫時性 HTTP 狀態碼（429 另有專屬 RateLimitError 分支）
_TRANSIENT_STATUS = {429, 500, 502, 503, 504}


def classify_and_downgrade(event, hint):
    """Sentry before_send：暫時性 OpenAI 錯誤 → warning + fingerprint；其餘原樣回傳（不丟棄）。"""
    exc_info = (hint or {}).get("exc_info")
    exc = exc_info[1] if exc_info else None
    if exc is None:
        return event

    # 限流（429）：OpenAI SDK 專屬 RateLimitError → rate-limit fingerprint
    if isinstance(exc, openai.RateLimitError):
        fp, tag = _FP_RATE
    # 其他帶 status_code 的 OpenAI API 錯誤（含 APIStatusError）：5xx/429 過載 → transient fingerprint
    elif isinstance(exc, openai.APIError) and getattr(exc, "status_code", None) in _TRANSIENT_STATUS:
        fp, tag = _FP_TRANSIENT
    else:
        return event  # 非暫時性 OpenAI 錯誤（含真 bug 4xx、一般例外）一律不動

    event["level"] = "warning"
    event["fingerprint"] = [fp]
    event.setdefault("tags", {})["openai_transient"] = tag
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
