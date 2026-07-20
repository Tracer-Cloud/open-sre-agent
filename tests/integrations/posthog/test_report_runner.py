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
