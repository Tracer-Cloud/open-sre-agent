"""Tests for interactive-shell command parsing.

Alpha mode removed the shell-command safety policy (allowlist / restricted /
mutating classification and the deny floor). Parsing now only decides *how* a
command runs — via ``argv`` or through a shell — and never blocks a command for
safety. The only non-execution outcome is empty input.
"""

from __future__ import annotations

from tools.interactive_shell.shell.parsing import (
    argv_for_repl_builtin_detection,
    parse_shell_command,
)


def test_parse_shell_command_detects_passthrough_prefix() -> None:
    parsed = parse_shell_command("!echo hello", is_windows=False)

    assert parsed.passthrough is True
    assert parsed.use_shell is True
    assert parsed.command == "echo hello"
    assert parsed.argv is None
    assert parsed.parse_error is None


def test_plain_command_uses_argv_without_shell() -> None:
    parsed = parse_shell_command("rm -rf /tmp/x", is_windows=False)

    assert parsed.passthrough is False
    assert parsed.use_shell is False
    assert parsed.argv == ["rm", "-rf", "/tmp/x"]
    assert parsed.parse_error is None


def test_restricted_command_is_no_longer_blocked() -> None:
    """``sudo`` and friends used to be a hard deny; alpha mode just runs them."""
    parsed = parse_shell_command("sudo systemctl restart nginx", is_windows=False)

    assert parsed.use_shell is False
    assert parsed.argv == ["sudo", "systemctl", "restart", "nginx"]
    assert parsed.parse_error is None


def test_operators_run_through_shell_without_block() -> None:
    parsed = parse_shell_command("ls | wc -l", is_windows=False)

    assert parsed.use_shell is True
    assert parsed.passthrough is False
    assert parsed.argv is None
    assert parsed.parse_error is None


def test_command_substitution_runs_through_shell() -> None:
    parsed = parse_shell_command("echo $(date)", is_windows=False)

    assert parsed.use_shell is True
    assert parsed.parse_error is None


def test_unbalanced_quotes_fall_back_to_shell() -> None:
    parsed = parse_shell_command('echo "unterminated', is_windows=False)

    assert parsed.use_shell is True
    assert parsed.parse_error is None


def test_empty_passthrough_is_parse_error() -> None:
    parsed = parse_shell_command("!", is_windows=False)

    assert parsed.parse_error is not None
    assert parsed.passthrough is True


def test_empty_command_is_parse_error() -> None:
    parsed = parse_shell_command("   ", is_windows=False)

    assert parsed.parse_error == "empty command."


def test_argv_for_repl_builtin_detection_splits_passthrough_for_cd() -> None:
    parsed = parse_shell_command("!cd /tmp", is_windows=False)
    assert argv_for_repl_builtin_detection(parsed=parsed, is_windows=False) == ["cd", "/tmp"]


def test_argv_for_repl_builtin_detection_returns_plain_argv() -> None:
    parsed = parse_shell_command("pwd", is_windows=False)
    assert argv_for_repl_builtin_detection(parsed=parsed, is_windows=False) == ["pwd"]


def test_argv_for_repl_builtin_detection_skips_operator_command() -> None:
    """A leading ``cd`` in an operator command must not be hijacked as a builtin."""
    parsed = parse_shell_command("cd /tmp && ls", is_windows=False)
    assert argv_for_repl_builtin_detection(parsed=parsed, is_windows=False) is None
