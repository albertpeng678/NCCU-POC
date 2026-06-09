"""
AsyncOpenAI singleton for NCCU-POC.

Design notes (HANDOFF Session 8 event-loop 教訓):
- 勿在 import 時建立 client（會綁到 import 時的 event loop，跨 loop 重用會崩）。
- 勿跨 event loop 重用同一個 client instance。
- 改用延遲建立（startup 時 get_client() 第一次呼叫）、同一 loop 內重用 singleton。
"""

import os
from typing import Optional

from openai import AsyncOpenAI

# Module-level singleton — lazily initialized by get_client().
_client: Optional[AsyncOpenAI] = None


def get_client() -> Optional[AsyncOpenAI]:
    """Return the AsyncOpenAI singleton, creating it on first call.

    Returns None when OPENAI_API_KEY is not set (e.g. in CI without secrets).
    """
    global _client
    if _client is not None:
        return _client

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None

    _client = AsyncOpenAI(
        api_key=api_key,
        max_retries=3,
        timeout=180.0,
    )
    return _client
