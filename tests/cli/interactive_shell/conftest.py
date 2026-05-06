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
