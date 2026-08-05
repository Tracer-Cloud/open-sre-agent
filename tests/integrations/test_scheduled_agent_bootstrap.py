"""Tests for scheduled headless agent runner routing."""

from __future__ import annotations

import pytest

import integrations.scheduled_agent_bootstrap as bootstrap
from platform.scheduler.agent_runner import AgentPayload


def test_manual_loop_payload_routes_to_manual_prompt_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _manual_runner(payload: AgentPayload) -> str:
        captured.update(payload)
        return "star report"

    def _sentry_runner(_payload: AgentPayload) -> str:
        raise AssertionError("manual loops must not fall back to Sentry digest")

    monkeypatch.setattr(bootstrap, "run_manual_prompt_loop", _manual_runner)
    monkeypatch.setattr(bootstrap, "run_sentry_morning_digest", _sentry_runner)

    result = bootstrap.run_scheduled_agent_digest(
        {
            "source": "scheduled_manual_loop",
            "loop_prompt": "Report GitHub stars.",
        }
    )

    assert result == "star report"
    assert captured["loop_prompt"] == "Report GitHub stars."
