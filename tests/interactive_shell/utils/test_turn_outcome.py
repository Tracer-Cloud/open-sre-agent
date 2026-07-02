"""Tests for terminal-turn analytics outcome formatting."""

from __future__ import annotations

from surfaces.interactive_shell.utils.telemetry.turn_outcome import (
    format_investigation_outcome,
    format_investigation_terminal_outcome,
    format_terminal_turn_outcome,
    format_wizard_cli_outcome,
    slash_command_is_interactive_wizard,
    slash_command_is_summary_only,
    truncate_analytics_text,
)


def test_slash_command_is_interactive_wizard() -> None:
    assert slash_command_is_interactive_wizard("/onboard")
    assert slash_command_is_interactive_wizard("/integrations setup")
    assert not slash_command_is_interactive_wizard("/health")
    assert not slash_command_is_interactive_wizard("/investigate generic")


def test_format_wizard_cli_outcome() -> None:
    assert "completed successfully" in format_wizard_cli_outcome(["onboard"], exit_code=0)
    assert "failed (exit 1)" in format_wizard_cli_outcome(["onboard"], exit_code=1)
    assert "cancelled" in format_wizard_cli_outcome(["onboard"], exit_code=None)


def test_format_investigation_outcome_background() -> None:
    text = format_investigation_outcome("generic", background=True)
    assert "started in background" in text
    assert "generic" in text


def test_format_investigation_outcome_failed_includes_reason() -> None:
    text = format_investigation_outcome(
        "generic",
        status="failed",
        error_message="jenkins is not configured",
    )
    assert text.startswith("investigation_failed (generic):")
    assert "jenkins is not configured" in text


def test_format_investigation_outcome_cancelled() -> None:
    text = format_investigation_outcome("alert.json", status="cancelled")
    assert text == "investigation_cancelled (alert.json): aborted by user"


def test_format_investigation_terminal_outcome_failed_two_line_shape() -> None:
    text = format_investigation_terminal_outcome(
        "/investigate generic",
        target="generic",
        ok=False,
        error_message="integration timeout",
        status="failed",
    )
    assert text.startswith("slash /investigate generic (failed)")
    assert "investigation_failed (generic):" in text
    assert "integration timeout" in text


def test_format_investigation_outcome_includes_root_cause() -> None:
    text = format_investigation_outcome(
        "generic",
        final_state={"root_cause": "Pod OOMKilled in checkout-api"},
    )
    assert "investigation completed" in text
    assert "OOMKilled" in text


def test_format_terminal_turn_outcome_prefers_hint() -> None:
    text = format_terminal_turn_outcome(
        "/onboard",
        kind="slash",
        ok=True,
        captured_output="",
        outcome_hint="opensre onboard: interactive wizard completed successfully",
    )
    assert text == "opensre onboard: interactive wizard completed successfully"


def test_slash_command_is_summary_only() -> None:
    assert slash_command_is_summary_only("/help")
    assert slash_command_is_summary_only("/help /model")
    assert slash_command_is_summary_only("/investigate generic")
    assert slash_command_is_summary_only("/onboard")
    assert not slash_command_is_summary_only("/status")


def test_format_terminal_turn_outcome_omits_help_table() -> None:
    text = format_terminal_turn_outcome(
        "/help",
        kind="slash",
        ok=True,
        captured_output="/exit — quit\n/model — change model",
    )
    assert text == "slash /help (succeeded)"


def test_format_investigation_outcome_includes_report_body() -> None:
    text = format_investigation_outcome(
        "generic",
        final_state={
            "root_cause": "Pod OOMKilled",
            "problem_md": "## Summary\nCheckout latency spiked due to memory pressure.",
        },
    )
    assert "OOMKilled" in text
    assert "Checkout latency spiked" in text


def test_format_terminal_turn_outcome_includes_captured_output() -> None:
    text = format_terminal_turn_outcome(
        "/status",
        kind="slash",
        ok=True,
        captured_output="integrations: datadog",
    )
    assert text.startswith("slash /status (succeeded)")
    assert "datadog" in text


def test_truncate_analytics_text() -> None:
    long_text = "x" * 100
    truncated = truncate_analytics_text(long_text, max_chars=50)
    assert len(truncated) <= 50
    assert truncated.endswith("[truncated]")
