"""Tests for the headless PostHog metric report runner (#3824)."""

from __future__ import annotations

import pytest

from integrations.posthog import report_runner


def test_build_report_prompt_defaults() -> None:
    prompt = report_runner.build_report_prompt({})
    assert "posthog-summary skill" in prompt
    assert "7d window" in prompt


def test_build_report_prompt_custom_period_and_metrics() -> None:
    prompt = report_runner.build_report_prompt({"stats_period": "30d", "metrics": "dau,signups"})
    assert "30d window" in prompt
    assert "dau,signups" in prompt


def test_require_posthog_configured_passes_with_posthog_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "integrations.posthog.report_prerequisites.configured_integration_services",
        lambda: ("posthog_mcp",),
    )
    report_runner._require_posthog_configured()


def test_require_posthog_configured_rejects_rest_only_posthog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "integrations.posthog.report_prerequisites.configured_integration_services",
        lambda: ("posthog",),
    )
    with pytest.raises(RuntimeError, match="PostHog MCP is not configured"):
        report_runner._require_posthog_configured()


def test_require_posthog_configured_raises_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "integrations.posthog.report_prerequisites.configured_integration_services",
        lambda: ("sentry",),
    )
    with pytest.raises(RuntimeError, match="PostHog MCP is not configured"):
        report_runner._require_posthog_configured()


def test_run_surfaces_unanswered_error_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """Billing/auth failures leave text without llm_run — don't hide it."""
    monkeypatch.setattr(
        "integrations.posthog.report_prerequisites.configured_integration_services",
        lambda: ("posthog_mcp",),
    )

    class _Result:
        answered = False
        primary_response_text = "Anthropic credit exhausted (provider billing/quota)."

    monkeypatch.setattr(
        report_runner,
        "_dispatch_headless_turn",
        lambda _message: _Result(),
    )

    with pytest.raises(RuntimeError, match="Anthropic credit exhausted"):
        report_runner.run_posthog_report({"stats_period": "7d"})


def test_dispatch_uses_shared_headless_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    """Match Sentry morning digest: build_default_headless_agent + harness.dispatch."""
    monkeypatch.setattr(
        "integrations.posthog.report_prerequisites.configured_integration_services",
        lambda: ("posthog_mcp",),
    )

    built: list[object] = []
    dispatched: list[str] = []

    class _Startup:
        session = object()

    class _Harness:
        def __init__(self, _config: object) -> None:
            pass

        def startup(self) -> _Startup:
            return _Startup()

        def attach_agent(self, agent: object) -> None:
            built.append(("attach", agent))

        def dispatch_message(self, message: str) -> object:
            dispatched.append(message)

            class _Result:
                answered = True
                primary_response_text = "I found: DAU up 12%"

            return _Result()

    def _build_agent(**kwargs: object) -> object:
        built.append(("build", kwargs))
        return object()

    monkeypatch.setattr(report_runner, "AgentHarness", _Harness)
    monkeypatch.setattr(report_runner, "build_default_headless_agent", _build_agent)

    report = report_runner.run_posthog_report({"stats_period": "7d"})

    assert report == "I found: DAU up 12%"
    assert any(step[0] == "build" for step in built)
    assert any(step[0] == "attach" for step in built)
    assert dispatched and "posthog-summary skill" in dispatched[0]
