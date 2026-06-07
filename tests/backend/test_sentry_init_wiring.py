# tests/backend/test_sentry_init_wiring.py
"""守門：main.py 的 sentry_sdk.init 確實接上 before_send + traces_sampler。
（行為由 test_sentry_before_send.py 單測；此處只確保有接線、避免日後被誤刪。）
"""
import pathlib

import backend.observability as obs

_MAIN = pathlib.Path(__file__).resolve().parents[2] / "backend" / "main.py"


def test_observability_exposes_callables():
    assert callable(obs.before_send)
    assert callable(obs.stream_traces_sampler)


def test_main_wires_before_send_and_traces_sampler():
    src = _MAIN.read_text(encoding="utf-8")
    assert "before_send=sentry_before_send" in src, "main.py 未接 before_send"
    assert "traces_sampler=stream_traces_sampler" in src, "main.py 未接 traces_sampler"
