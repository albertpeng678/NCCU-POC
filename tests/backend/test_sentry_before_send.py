# tests/backend/test_sentry_before_send.py
"""Sentry before_send：把預期內的暫時性 Gemini 錯誤（429/503/spending cap）降成 warning
+ 穩定 fingerprint（歸群、不再 Escalating 洗版），真 bug 維持 error 不動。降級不丟棄。
純函式（不依賴 sentry init），故可直接 import 測。
"""
from google.genai import errors as genai_errors

from backend.observability import classify_and_downgrade, stream_traces_sampler


def _hint(exc):
    return {"exc_info": (type(exc), exc, None)}


def test_429_downgraded_to_warning_with_fingerprint():
    exc = genai_errors.ClientError(429, {"error": {"message": "Resource exhausted"}})
    ev = classify_and_downgrade({}, _hint(exc))
    assert ev["level"] == "warning"
    assert ev["fingerprint"] == ["gemini-rate-limit-429"]
    assert ev["tags"]["gemini_transient"] == "rate-limit"


def test_503_high_demand_downgraded():
    exc = genai_errors.ServerError(503, {"error": {"message": "The model is overloaded"}})
    ev = classify_and_downgrade({}, _hint(exc))
    assert ev["level"] == "warning"
    assert ev["fingerprint"] == ["gemini-high-demand-503"]
    assert ev["tags"]["gemini_transient"] == "high-demand"


def test_spending_limit_message_downgraded():
    # spending limit 文案優先於純 429 → 歸到 spending-cap
    exc = genai_errors.ClientError(429, {"error": {"message": "You exceeded your monthly spending limit"}})
    ev = classify_and_downgrade({}, _hint(exc))
    assert ev["fingerprint"] == ["gemini-spending-cap"]
    assert ev["tags"]["gemini_transient"] == "spending-cap"


def test_real_bug_apierror_400_not_modified():
    exc = genai_errors.ClientError(400, {"error": {"message": "Invalid argument"}})
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
    exc = genai_errors.ServerError(503, {"error": {"message": "overloaded"}})
    ev = classify_and_downgrade({"tags": {"foo": "bar"}}, _hint(exc))
    assert ev["tags"]["foo"] == "bar"
    assert ev["tags"]["gemini_transient"] == "high-demand"


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
