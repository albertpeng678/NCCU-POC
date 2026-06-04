# tests/backend/test_streaming_deps.py
def test_sse_starlette_importable():
    """EventSourceResponse 必須可 import（sse-starlette 已安裝）。"""
    from sse_starlette.sse import EventSourceResponse
    assert EventSourceResponse is not None
