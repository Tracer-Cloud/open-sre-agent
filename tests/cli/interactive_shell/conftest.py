"""Shared fixtures for interactive-shell tests."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from io import StringIO

import pytest
from rich.console import Console

from app.cli.interactive_shell.runtime.session import ReplSession


@dataclass
class ReplTestHarness:
    session: ReplSession
    console: Console
    output: StringIO

    def run(self, cmd_fn, args=()) -> bool:
        return cmd_fn(
            self.session,
            self.console,
            list(args),
        )

    def printed(self) -> str:
        return self.output.getvalue()


@pytest.fixture
def repl_harness() -> ReplTestHarness:
    output = StringIO()

    console = Console(
        file=output,
        force_terminal=False,
        highlight=False,
    )

    session = ReplSession()

    return ReplTestHarness(
        session=session,
        console=console,
        output=output,
    )


@pytest.fixture(autouse=True)
def _repl_execution_policy_auto_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Elevated REPL actions prompt for confirmation; stdin is non-TTY under pytest."""
    monkeypatch.setattr(
        "app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_policy.DEFAULT_CONFIRM_FN",
        lambda _prompt: "y",
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
