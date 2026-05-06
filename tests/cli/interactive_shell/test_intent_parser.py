"""Unit tests for natural-language intent parsing helpers."""

from __future__ import annotations

from app.cli.interactive_shell.intent_parser import (
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
