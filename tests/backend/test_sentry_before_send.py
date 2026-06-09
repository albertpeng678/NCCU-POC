# tests/backend/test_sentry_before_send.py
"""Sentry before_send：把預期內的暫時性 OpenAI 錯誤（429 限流 / 5xx 過載）降成 warning
+ 穩定 fingerprint（歸群、不再 Escalating 洗版），真 bug 維持 error 不動。降級不丟棄。
純函式（不依賴 sentry init），故可直接 import 測。
"""
import httpx
import openai

from backend.observability import classify_and_downgrade, stream_traces_sampler


_REQ = httpx.Request("POST", "https://api.openai.com/v1/responses")


def _hint(exc):
    return {"exc_info": (type(exc), exc, None)}


def _rate_limit():
    return openai.RateLimitError(
        "rate limited", response=httpx.Response(429, request=_REQ), body=None
    )


def _status(code, message="error"):
    return openai.APIStatusError(
        message, response=httpx.Response(code, request=_REQ), body=None
    )


def test_429_rate_limit_downgraded_to_warning_with_fingerprint():
    ev = classify_and_downgrade({}, _hint(_rate_limit()))
    assert ev["level"] == "warning"
    assert ev["fingerprint"] == ["openai-rate-limit-429"]
    assert ev["tags"]["openai_transient"] == "rate-limit"


def test_503_high_demand_downgraded():
    ev = classify_and_downgrade({}, _hint(_status(503, "The model is overloaded")))
    assert ev["level"] == "warning"
    assert ev["fingerprint"] == ["openai-transient-5xx"]
    assert ev["tags"]["openai_transient"] == "high-demand"


def test_500_transient_downgraded():
    ev = classify_and_downgrade({}, _hint(_status(500, "internal server error")))
    assert ev["level"] == "warning"
    assert ev["fingerprint"] == ["openai-transient-5xx"]
    assert ev["tags"]["openai_transient"] == "high-demand"


def test_real_bug_apistatus_400_not_modified():
    exc = _status(400, "Invalid argument")
    ev = classify_and_downgrade({"level": "error"}, _hint(exc))
    assert ev["level"] == "error"
    assert "fingerprint" not in ev


def test_generic_exception_not_modified():
    ev = classify_and_downgrade({"level": "error"}, _hint(KeyError("courses")))
    assert ev == {"level": "error"}


def test_missing_exc_info_passthrough():
    ev = classify_and_downgrade({"level": "error"}, {})
    assert ev == {"level": "error"}


def test_existing_tags_preserved():
    ev = classify_and_downgrade({"tags": {"foo": "bar"}}, _hint(_status(503, "overloaded")))
    assert ev["tags"]["foo"] == "bar"
    assert ev["tags"]["openai_transient"] == "high-demand"


# ---- traces_sampler：串流端點降取樣 ----

def test_stream_paths_sampled_low():
    assert stream_traces_sampler({"asgi_scope": {"path": "/recommend/stream"}}) == 0.1
    assert stream_traces_sampler({"asgi_scope": {"path": "/qa/stream"}}) == 0.1


def test_non_stream_paths_full():
    assert stream_traces_sampler({"asgi_scope": {"path": "/recommend"}}) == 1.0
    assert stream_traces_sampler({"asgi_scope": {"path": "/qa"}}) == 1.0


def test_sampler_robust_default():
    assert stream_traces_sampler({}) == 1.0
    assert stream_traces_sampler({"transaction_context": {"name": "GET /qa/stream"}}) == 0.1
