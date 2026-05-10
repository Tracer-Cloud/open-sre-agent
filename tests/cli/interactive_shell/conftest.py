"""Shared fixtures for interactive-shell tests."""

from __future__ import annotations

import sys

import pytest


@pytest.fixture(autouse=True)
def _repl_execution_policy_auto_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Elevated REPL actions prompt for confirmation; stdin is non-TTY under pytest."""
    monkeypatch.setattr(
        "app.cli.interactive_shell.execution_policy.DEFAULT_CONFIRM_FN",
        lambda _prompt: "y",
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)


@pytest.fixture(autouse=True)
def _disable_llm_routing_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable the LLM intent classifier for all interactive-shell tests by default.

    Existing router tests assert exact rule names and confidence scores from
    the regex fallback path; running them against a live LLM would cause
    non-deterministic failures and unnecessary API usage.

    Tests that specifically exercise LLM behaviour must mock
    ``app.services.llm_client.get_llm_for_tools`` or
    ``app.cli.interactive_shell.llm_intent_classifier.classify_intent_with_llm``
    directly, and should patch ``app.cli.interactive_shell.router._LLM_ROUTING_DISABLED``
    to ``False`` when they need the live routing pipeline.
    """
    monkeypatch.setattr("app.cli.interactive_shell.router._LLM_ROUTING_DISABLED", True)
