from __future__ import annotations

from typing import cast

import pytest

from app.pipeline import runners
from app.state import AgentState


def test_run_chat_initializes_sentry_and_captures_unhandled_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentry_init_calls: list[None] = []
    captured_errors: list[BaseException] = []
    expected_error = RuntimeError("router failed")

    def failing_router(_state: AgentState) -> dict[str, object]:
        raise expected_error

    monkeypatch.setattr(runners, "init_sentry", lambda **_kw: sentry_init_calls.append(None))
    monkeypatch.setattr(runners, "capture_exception", captured_errors.append)
    monkeypatch.setattr(runners, "router_node", failing_router)

    with pytest.raises(RuntimeError, match="router failed"):
        runners.run_chat(cast(AgentState, {}))

    assert sentry_init_calls == [None]
    assert captured_errors == [expected_error]
