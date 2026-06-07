# tests/backend/test_qa_error_classify.py
"""classify_qa_error：把 Q&A 例外映射成語意 error_type（rate_limited / timeout / unknown），
讓前端能走差異化分支（暫時性過載→鼓勵重試 vs 其他）。no_match 不由此函式產生（源自 grounding 空）。
"""
import asyncio
import httpx
from google.genai import errors as genai_errors

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
