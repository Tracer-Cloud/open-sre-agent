"""Tests for interactive-shell command safety policy."""

from __future__ import annotations

from app.cli.interactive_shell.shell_policy import (
    classify_command,
    evaluate_policy,
    parse_shell_command,
)


def test_parse_shell_command_detects_passthrough_prefix() -> None:
    parsed = parse_shell_command("!echo hello", is_windows=False)

    assert parsed.passthrough is True
    assert parsed.command == "echo hello"
    assert parsed.argv is None
    assert parsed.parse_error is None


def test_parse_shell_command_rejects_operators_in_safe_mode() -> None:
    parsed = parse_shell_command("ls | wc -l", is_windows=False)
    decision = evaluate_policy(parsed=parsed)

    assert decision.allow is False
    assert "shell operators" in (decision.reason or "")


def test_classify_command_handles_git_read_only_and_mutating() -> None:
    assert classify_command(["git", "status"]) == "read_only"
    assert classify_command(["git", "commit", "-m", "x"]) == "mutating"


def test_evaluate_policy_blocks_unknown_command_by_default() -> None:
    parsed = parse_shell_command("mycustomcmd --check", is_windows=False)
    decision = evaluate_policy(parsed=parsed)

    assert decision.allow is False
    assert decision.classification == "unknown"
    assert "allowlist" in (decision.reason or "")
