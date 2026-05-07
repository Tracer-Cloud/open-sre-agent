"""Unit tests for REPL execution policy composition."""

from __future__ import annotations

import io

from rich.console import Console

from app.cli.interactive_shell.execution_policy import (
    evaluate_investigation_launch,
    evaluate_shell_command,
    evaluate_slash_tier,
    evaluate_synthetic_test_launch,
    execution_allowed,
    resolve_slash_execution_tier,
)
from app.cli.interactive_shell.execution_tier import ExecutionTier
from app.cli.interactive_shell.session import ReplSession


def test_read_only_shell_is_allow() -> None:
    r = evaluate_shell_command("pwd")
    assert r.verdict == "allow"
    assert r.action_type == "shell"


def test_restricted_shell_is_deny() -> None:
    r = evaluate_shell_command("sudo ls /")
    assert r.verdict == "deny"


def test_mutating_shell_is_ask() -> None:
    r = evaluate_shell_command("rm -rf /tmp/x")
    assert r.verdict == "ask"
    assert r.shell_classification == "mutating"


def test_passthrough_shell_is_ask() -> None:
    r = evaluate_shell_command("!echo hi")
    assert r.verdict == "ask"


def test_slash_exempt_is_allow() -> None:
    r = evaluate_slash_tier(ExecutionTier.EXEMPT)
    assert r.verdict == "allow"


def test_slash_elevated_is_ask() -> None:
    r = evaluate_slash_tier(ExecutionTier.ELEVATED)
    assert r.verdict == "ask"


def test_model_show_resolves_safe() -> None:
    tier = resolve_slash_execution_tier("/model", [], ExecutionTier.SAFE)
    assert tier == ExecutionTier.SAFE


def test_model_set_resolves_elevated() -> None:
    tier = resolve_slash_execution_tier("/model", ["set", "anthropic"], ExecutionTier.SAFE)
    assert tier == ExecutionTier.ELEVATED


def test_integrations_verify_resolves_elevated() -> None:
    tier = resolve_slash_execution_tier("/integrations", ["verify"], ExecutionTier.SAFE)
    assert tier == ExecutionTier.ELEVATED


def test_investigation_launch_is_ask() -> None:
    r = evaluate_investigation_launch(action_type="investigation")
    assert r.verdict == "ask"
    assert r.action_type == "investigation"


def test_synthetic_is_ask() -> None:
    r = evaluate_synthetic_test_launch()
    assert r.verdict == "ask"


def test_trust_mode_skips_ask() -> None:
    session = ReplSession()
    session.trust_mode = True
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    r = evaluate_slash_tier(ExecutionTier.ELEVATED)
    assert execution_allowed(
        r,
        session=session,
        console=console,
        action_summary="/investigate x",
        confirm_fn=lambda _: "n",
        is_tty=True,
    )


def test_non_tty_blocks_ask() -> None:
    session = ReplSession()
    session.trust_mode = False
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    r = evaluate_slash_tier(ExecutionTier.ELEVATED)
    assert not execution_allowed(
        r,
        session=session,
        console=console,
        action_summary="/save out.md",
        is_tty=False,
    )
    assert "not a TTY" in buf.getvalue()


def test_tty_ask_combines_summary_and_reason_on_one_line() -> None:
    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    r = evaluate_slash_tier(ExecutionTier.ELEVATED)
    assert not execution_allowed(
        r,
        session=session,
        console=console,
        action_summary="/integrations verify foo",
        confirm_fn=lambda _: "n",
        is_tty=True,
    )
    out = buf.getvalue()
    assert "Action:" not in out
    assert "/integrations verify foo" in out
    assert "Confirm" in out
    assert "configuration" in out


def test_tty_ask_when_action_already_listed_omits_repeat_of_summary() -> None:
    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)
    r = evaluate_slash_tier(ExecutionTier.ELEVATED)
    assert not execution_allowed(
        r,
        session=session,
        console=console,
        action_summary="/integrations verify foo",
        confirm_fn=lambda _: "n",
        is_tty=True,
        action_already_listed=True,
    )
    out = buf.getvalue()
    assert "/integrations verify foo" not in out
    assert "Confirm:" in out
    assert "configuration" in out
