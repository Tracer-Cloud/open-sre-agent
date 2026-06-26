"""Unit tests for REPL execution policy composition.

The REPL is default-allow: policy functions resolve to ``allow`` with no
confirmation prompt, except for a hard ``deny`` floor (restricted shell commands
and unparseable shell input). The ``ask`` verdict and its confirmation UX are
retained for ``trust_mode`` / future opt-in stricter policy, so those paths are
covered here using explicitly-constructed ``ask`` results.
"""

from __future__ import annotations

import io

from rich.console import Console

from cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_policy import (
    ExecutionPolicyResult,
    evaluate_code_agent_launch,
    evaluate_investigation_launch,
    evaluate_llm_runtime_switch,
    evaluate_shell_command,
    evaluate_slash_tier,
    evaluate_synthetic_test_launch,
    execution_allowed,
    resolve_slash_execution_tier,
)
from cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_tier import (
    ExecutionTier,
)
from cli.interactive_shell.runtime.session import ReplSession


def _ask_result() -> ExecutionPolicyResult:
    """An explicit ``ask`` verdict (the default policy no longer emits these)."""
    return ExecutionPolicyResult(
        verdict="ask",
        action_type="slash",
        reason="this command may change configuration or run heavy work",
    )


# --- Default-allow policy decisions -----------------------------------------


def test_read_only_shell_is_allow() -> None:
    r = evaluate_shell_command("pwd")
    assert r.verdict == "allow"
    assert r.action_type == "shell"


def test_restricted_shell_is_deny() -> None:
    r = evaluate_shell_command("sudo ls /")
    assert r.verdict == "deny"


def test_unparseable_shell_is_deny() -> None:
    """Shell operators cannot be safely parsed, so they stay denied."""
    r = evaluate_shell_command("ls | grep x")
    assert r.verdict == "deny"


def test_mutating_shell_is_allow() -> None:
    r = evaluate_shell_command("rm -rf /tmp/x")
    assert r.verdict == "allow"
    assert r.shell_classification == "mutating"


def test_passthrough_shell_is_allow() -> None:
    r = evaluate_shell_command("!echo hi")
    assert r.verdict == "allow"


def test_slash_exempt_is_allow() -> None:
    r = evaluate_slash_tier(ExecutionTier.EXEMPT)
    assert r.verdict == "allow"


def test_slash_elevated_is_allow() -> None:
    r = evaluate_slash_tier(ExecutionTier.ELEVATED)
    assert r.verdict == "allow"


def test_model_show_resolves_safe() -> None:
    tier = resolve_slash_execution_tier("/model", [], ExecutionTier.SAFE)
    assert tier == ExecutionTier.SAFE


def test_model_set_resolves_elevated() -> None:
    tier = resolve_slash_execution_tier("/model", ["set", "anthropic"], ExecutionTier.SAFE)
    assert tier == ExecutionTier.ELEVATED


def test_integrations_verify_resolves_elevated() -> None:
    tier = resolve_slash_execution_tier("/integrations", ["verify"], ExecutionTier.SAFE)
    assert tier == ExecutionTier.ELEVATED


def test_investigation_launch_is_allow() -> None:
    r = evaluate_investigation_launch(action_type="investigation")
    assert r.verdict == "allow"
    assert r.action_type == "investigation"


def test_investigation_launch_user_initiated_is_allow() -> None:
    r = evaluate_investigation_launch(action_type="investigation", user_initiated=True)
    assert r.verdict == "allow"
    assert r.action_type == "investigation"


def test_sample_alert_user_initiated_is_allow() -> None:
    r = evaluate_investigation_launch(action_type="sample_alert", user_initiated=True)
    assert r.verdict == "allow"
    assert r.action_type == "sample_alert"


def test_synthetic_is_allow() -> None:
    r = evaluate_synthetic_test_launch()
    assert r.verdict == "allow"


def test_code_agent_is_allow() -> None:
    r = evaluate_code_agent_launch()
    assert r.verdict == "allow"


def test_llm_runtime_switch_is_allow() -> None:
    r = evaluate_llm_runtime_switch(action_type="llm_runtime")
    assert r.verdict == "allow"


# --- execution_allowed: default-allow runs without prompting ----------------


def test_allow_verdict_runs_without_prompt() -> None:
    session = ReplSession()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False)

    def _confirm(_: str) -> str:  # pragma: no cover - must never be called
        raise AssertionError("default-allow must not prompt for confirmation")

    r = evaluate_slash_tier(ExecutionTier.ELEVATED)
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
    r = evaluate_slash_tier(ExecutionTier.ELEVATED)
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
    r = evaluate_shell_command("sudo rm -rf /")
    assert not execution_allowed(
        r,
        session=session,
        console=console,
        action_summary="sudo rm -rf /",
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
