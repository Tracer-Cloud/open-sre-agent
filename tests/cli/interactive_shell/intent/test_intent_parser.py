"""Unit tests for natural-language intent parsing helpers."""

from __future__ import annotations

import pytest

from app.cli.interactive_shell.intent.intent_parser import (
    SAMPLE_ALERT_RE,
    extract_implementation_request,
    extract_shell_command,
    normalize_shell_command,
    shutil,
    split_prompt_clauses,
)
from app.cli.interactive_shell.intent.interaction_models import PromptClause


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


def test_extract_implementation_request_matches_explicit_implement_phrase() -> None:
    action = extract_implementation_request(
        PromptClause(text="please implement /history search", position=3)
    )

    assert action is not None
    assert action.kind == "implementation"
    assert action.content == "/history search"
    assert action.position == 10


def test_extract_implementation_request_allows_context_dependent_bare_implement() -> None:
    action = extract_implementation_request(PromptClause(text="implement", position=0))

    assert action is not None
    assert action.kind == "implementation"
    assert action.content == "implement"


def test_code_editor_command_is_not_implementation_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _command: "/usr/bin/code")
    clause = PromptClause(text="code .", position=0)

    assert extract_implementation_request(clause) is None
    action = extract_shell_command(clause)
    assert action is not None
    assert action.kind == "shell"
    assert action.content == "code ."


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
