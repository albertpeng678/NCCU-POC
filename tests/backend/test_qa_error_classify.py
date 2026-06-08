# tests/backend/test_qa_error_classify.py
"""classify_qa_error：把 Q&A 例外映射成語意 error_type（rate_limited / timeout / unknown），
讓前端能走差異化分支（暫時性過載→鼓勵重試 vs 其他）。no_match 不由此函式產生（源自 grounding 空）。
"""
import asyncio
import httpx
from google.genai import errors as genai_errors
from openai import RateLimitError as _OAIRateLimit, APITimeoutError as _OAITimeout, APIStatusError as _OAIStatus

from backend.qa import classify_qa_error


def test_apierror_429_and_503_are_rate_limited():
    assert classify_qa_error(genai_errors.ClientError(429, {"error": {"message": "Resource exhausted"}})) == "rate_limited"
    assert classify_qa_error(genai_errors.ServerError(503, {"error": {"message": "overloaded"}})) == "rate_limited"


def test_apierror_other_code_is_unknown():
    assert classify_qa_error(genai_errors.ClientError(400, {"error": {"message": "bad"}})) == "unknown"


def test_httpx_and_asyncio_timeouts():
    assert classify_qa_error(httpx.TimeoutException("x")) == "timeout"
    assert classify_qa_error(httpx.ReadTimeout("x")) == "timeout"
    assert classify_qa_error(asyncio.TimeoutError()) == "timeout"


def test_message_mentioning_timeout_is_timeout():
    assert classify_qa_error(Exception("request deadline exceeded")) == "timeout"
    assert classify_qa_error(RuntimeError("connection timeout")) == "timeout"


def test_generic_and_none_are_unknown():
    assert classify_qa_error(ValueError("x")) == "unknown"
    assert classify_qa_error(None) == "unknown"


# ──── OpenAI error types (Fix 1) ────────────────────────────────────────────

def _make_response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code, request=httpx.Request("GET", "http://test.example.com"))


def test_openai_rate_limit_error_is_rate_limited():
    """openai.RateLimitError（429）→ rate_limited。"""
    exc = _OAIRateLimit("rate limit exceeded", response=_make_response(429), body=None)
    assert classify_qa_error(exc) == "rate_limited"


def test_openai_api_timeout_error_is_timeout():
    """openai.APITimeoutError → timeout。"""
    exc = _OAITimeout(httpx.Request("GET", "http://test.example.com"))
    assert classify_qa_error(exc) == "timeout"


def test_openai_api_status_error_429_is_rate_limited():
    """openai.APIStatusError status_code=429 → rate_limited。"""
    exc = _OAIStatus("rate limited", response=_make_response(429), body=None)
    assert classify_qa_error(exc) == "rate_limited"


def test_openai_api_status_error_503_is_rate_limited():
    """openai.APIStatusError status_code=503（暫時性過載）→ rate_limited。"""
    exc = _OAIStatus("service unavailable", response=_make_response(503), body=None)
    assert classify_qa_error(exc) == "rate_limited"


def test_openai_api_status_error_500_is_rate_limited():
    """openai.APIStatusError 5xx（暫時性）→ rate_limited（前端走重試分支）。"""
    exc = _OAIStatus("internal server error", response=_make_response(500), body=None)
    assert classify_qa_error(exc) == "rate_limited"


def test_openai_api_status_error_400_is_unknown():
    """openai.APIStatusError 400（非暫時性）→ unknown。"""
    exc = _OAIStatus("bad request", response=_make_response(400), body=None)
    assert classify_qa_error(exc) == "unknown"
