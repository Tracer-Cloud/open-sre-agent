"""Tests for the execution-policy interaction layer (``execution_allowed``).

These cover the terminal-facing half of the execution gate: console output and
the confirmation prompt. The pure decision is tested in
``tests/tools/interactive_shell/shared/test_execution_policy.py``.
"""

from __future__ import annotations

import io

from rich.console import Console

from core.agent_harness.session import ReplSession
from interactive_shell.ui.execution_confirm import execution_allowed
from tools.interactive_shell.shared import (
    ExecutionPolicyResult,
    allow_tool,
)


def _ask_result() -> ExecutionPolicyResult:
    """An explicit ``ask`` verdict (the default policy no longer emits these)."""
    return ExecutionPolicyResult(
        verdict="ask",
        tool_type="slash",
        reason="this command may change configuration or run heavy work",
    )


# --- execution_allowed: default-allow runs without prompting ----------------


def test_allow_verdict_runs_without_prompt() -> None:
    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    def _confirm(_: str) -> str:  # pragma: no cover - must never be called
        raise AssertionError("default-allow must not prompt for confirmation")

    r = allow_tool("slash")
    assert execution_allowed(
        r,
        session=session,
        console=console,
        action_summary="/integrations verify foo",
        confirm_fn=_confirm,
        is_tty=True,
    )
    assert "Confirm" not in buf.getvalue()


def test_non_tty_allows_default_policy() -> None:
    """Default-allow no longer fails closed on non-interactive stdin."""
    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    r = allow_tool("slash")
    assert execution_allowed(
        r,
        session=session,
        console=console,
        action_summary="/save out.md",
        is_tty=False,
    )


def test_deny_verdict_blocks() -> None:
    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    # The default policy never emits a deny; construct one explicitly to cover
    # the execution_allowed deny path.
    r = ExecutionPolicyResult(
        verdict="deny",
        tool_type="shell",
        reason="empty command.",
        hint="Enter a command to run.",
    )
    assert not execution_allowed(
        r,
        session=session,
        console=console,
        action_summary="!",
        is_tty=True,
    )
    assert "blocked" in buf.getvalue()


# --- Retained ask machinery (reachable only via explicit ask) ---------------


def test_explicit_ask_trust_mode_allows() -> None:
    session = ReplSession()
    session.trust_mode = True
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    assert execution_allowed(
        _ask_result(),
        session=session,
        console=console,
        action_summary="/investigate x",
        confirm_fn=lambda _: "n",
        is_tty=True,
    )


def test_explicit_ask_non_tty_blocks() -> None:
    session = ReplSession()
    session.trust_mode = False
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    assert not execution_allowed(
        _ask_result(),
        session=session,
        console=console,
        action_summary="/save out.md",
        is_tty=False,
    )
    assert "not a TTY" in buf.getvalue()


def test_explicit_ask_tty_accepts_empty_confirmation() -> None:
    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    captured: list[str] = []

    def _confirm(prompt: str) -> str:
        captured.append(prompt)
        return ""

    assert execution_allowed(
        _ask_result(),
        session=session,
        console=console,
        action_summary="/integrations verify foo",
        confirm_fn=_confirm,
        is_tty=True,
    )
    assert captured == ["Proceed? [Y/n] "]


def test_explicit_ask_tty_rejects_explicit_no() -> None:
    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    assert not execution_allowed(
        _ask_result(),
        session=session,
        console=console,
        action_summary="/integrations verify foo",
        confirm_fn=lambda _: "n",
        is_tty=True,
    )
    assert "cancelled" in buf.getvalue()
