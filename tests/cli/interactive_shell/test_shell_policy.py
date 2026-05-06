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


def test_find_exec_wrapper_is_blocked() -> None:
    """find -exec can spawn arbitrary child processes; must be blocked in safe mode."""
    parsed = parse_shell_command("find /tmp -exec rm -rf {} +", is_windows=False)
    decision = evaluate_policy(parsed=parsed)

    assert decision.allow is False
    assert decision.classification == "mutating"
    assert decision.reason == "mutating commands are blocked in safe mode."


def test_env_exec_wrapper_is_blocked() -> None:
    """env <cmd> execs arbitrary programs; must be blocked in safe mode."""
    parsed = parse_shell_command("env rm /tmp/foo", is_windows=False)
    decision = evaluate_policy(parsed=parsed)

    assert decision.allow is False
    assert decision.classification == "mutating"
    assert decision.reason == "mutating commands are blocked in safe mode."


def test_classify_command_marks_find_and_env_as_mutating() -> None:
    assert classify_command(["find", "/tmp", "-name", "*.log"]) == "mutating"
    assert classify_command(["env", "MY_VAR=1", "echo", "hello"]) == "mutating"


def test_evaluate_policy_blocks_restricted_command_sudo() -> None:
    parsed = parse_shell_command("sudo ls /tmp", is_windows=False)
    decision = evaluate_policy(parsed=parsed)

    assert decision.allow is False
    assert decision.classification == "restricted"
    assert decision.reason == "Not allowed for assistant-run shell."


def test_evaluate_policy_blocks_restricted_command_dd() -> None:
    parsed = parse_shell_command("dd if=/dev/zero of=/dev/null count=1", is_windows=False)
    decision = evaluate_policy(parsed=parsed)

    assert decision.allow is False
    assert decision.classification == "restricted"
    assert decision.reason == "Not allowed for assistant-run shell."


def test_classify_command_aws_ec2_describe_instances() -> None:
    assert classify_command(["aws", "ec2", "describe-instances"]) == "read_only"


def test_classify_command_aws_s3_ls() -> None:
    assert classify_command(["aws", "s3", "ls"]) == "read_only"


def test_classify_command_aws_global_flags_then_describe() -> None:
    assert (
        classify_command(
            ["aws", "--region", "us-east-1", "ec2", "describe-instances"],
        )
        == "read_only"
    )


def test_classify_command_aws_s3_cp_mutating() -> None:
    assert classify_command(["aws", "s3", "cp", "s3://b/a", "./a"]) == "mutating"


def test_classify_command_aws_configure_is_mutating() -> None:
    assert classify_command(["aws", "configure"]) == "mutating"


def test_aws_cli_positional_handles_double_dash_equals_form() -> None:
    parsed = parse_shell_command(
        "aws ec2 describe-instances --output json --query Reservations",
        is_windows=False,
    )
    decision = evaluate_policy(parsed=parsed)

    assert decision.allow is True
    assert decision.classification == "read_only"


def test_classify_command_sort_is_mutating_for_file_output_flags() -> None:
    assert classify_command(["sort", "-o", "/etc/cron.d/out", "in.txt"]) == "mutating"


def test_aws_no_sign_request_preserves_s3_ls_positional_pair() -> None:
    assert classify_command(["aws", "--no-sign-request", "s3", "ls"]) == "read_only"


def test_evaluate_policy_blocks_sort_even_without_shell_redirection() -> None:
    parsed = parse_shell_command("sort -o /tmp/out /tmp/in", is_windows=False)
    decision = evaluate_policy(parsed=parsed)

    assert decision.allow is False
    assert decision.classification == "mutating"
