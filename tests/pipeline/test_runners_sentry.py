from __future__ import annotations

from typing import cast

import pytest

from app.pipeline import runners
from app.state import AgentState
from app.utils import errors


def test_run_chat_initializes_sentry_and_captures_unhandled_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentry_init_calls: list[None] = []
    captured_errors: list[BaseException] = []
    expected_error = RuntimeError("chat failed")

    def failing_chat(_state: AgentState) -> AgentState:
        raise expected_error

    def capture_stub(exc: BaseException, **_kwargs: object) -> None:
        captured_errors.append(exc)

    import app.pipeline.pipeline as pipeline_module

    monkeypatch.setattr(runners, "init_sentry", lambda **_kw: sentry_init_calls.append(None))
    monkeypatch.setattr(errors, "capture_exception", capture_stub)
    monkeypatch.setattr(pipeline_module, "run_chat", failing_chat)

    with pytest.raises(RuntimeError, match="chat failed"):
        runners.run_chat(cast(AgentState, {}))

    assert sentry_init_calls == [None]
    assert captured_errors == [expected_error]
