"""Unit tests for natural-language intent parsing helpers."""

from __future__ import annotations

from app.cli.interactive_shell.intent_parser import (
    SAMPLE_ALERT_RE,
    normalize_shell_command,
    split_prompt_clauses,
)


def test_split_prompt_clauses_preserves_positions() -> None:
    msg = "  check health AND  list services "
    clauses = split_prompt_clauses(msg)
    assert len(clauses) == 2
    assert clauses[0].text == "check health"
    assert clauses[1].text == "list services"
    assert msg.index(clauses[0].text) == clauses[0].position


def test_normalize_shell_command_rejects_multiline() -> None:
    assert normalize_shell_command("ls\npwd") is None


def test_normalize_shell_command_strips_ticks() -> None:
    assert normalize_shell_command("`whoami`") == "whoami"


class TestSampleAlertRE:
    """SAMPLE_ALERT_RE is now the single canonical source for sample-alert launch
    detection (shared by both action_planner and the terminal_intent routing
    surface). These fixtures guard against accidental pattern drift."""

    def test_matches_canonical_sample_alert_phrases(self) -> None:
        positives = [
            "try a sample alert",
            "run a sample alert",
            "launch a simple alert",
            "fire a demo alert",
            "start a test alert",
            "send a sample event",
            "trigger a demo event",
            "okay launch a simple alert",
        ]
        for phrase in positives:
            assert SAMPLE_ALERT_RE.search(phrase) is not None, (
                f"SAMPLE_ALERT_RE should match: {phrase!r}"
            )

    def test_does_not_match_real_incident_descriptions(self) -> None:
        negatives = [
            "the checkout API returned a 502 error",
            "CPU spiked on orders-api",
            "why is the database slow?",
            "investigate the latency spike",
        ]
        for phrase in negatives:
            assert SAMPLE_ALERT_RE.search(phrase) is None, (
                f"SAMPLE_ALERT_RE should NOT match: {phrase!r}"
            )
