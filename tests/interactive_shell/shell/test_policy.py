"""Tests for shell-specific execution policy.

Alpha mode allows every shell command (read-only, mutating, restricted,
operators, substitution) and only rejects genuinely empty input.
"""

from __future__ import annotations

from tools.interactive_shell.shell.policy import evaluate_shell_command


def test_read_only_shell_is_allow() -> None:
    r = evaluate_shell_command("pwd")
    assert r.verdict == "allow"
    assert r.tool_type == "shell"


def test_restricted_shell_is_allow() -> None:
    """Alpha mode removed the restricted deny floor; ``sudo`` now runs."""
    r = evaluate_shell_command("sudo ls /")
    assert r.verdict == "allow"
    assert r.shell_classification == "unrestricted"


def test_operator_shell_is_allow() -> None:
    """Shell operators run through a shell instead of being blocked."""
    r = evaluate_shell_command("ls | grep x")
    assert r.verdict == "allow"


def test_mutating_shell_is_allow() -> None:
    r = evaluate_shell_command("rm -rf /tmp/x")
    assert r.verdict == "allow"
    assert r.shell_classification == "unrestricted"


def test_passthrough_shell_is_allow() -> None:
    r = evaluate_shell_command("!echo hi")
    assert r.verdict == "allow"


def test_empty_shell_input_is_deny() -> None:
    """Only genuinely empty input is rejected (input validation, not a guardrail)."""
    r = evaluate_shell_command("!")
    assert r.verdict == "deny"
