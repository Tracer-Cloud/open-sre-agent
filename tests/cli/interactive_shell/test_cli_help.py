"""Tests for REPL CLI help assistant (reference text, no LLM calls)."""

from __future__ import annotations

from app.cli.interactive_shell.cli_help import build_cli_reference_text


def test_build_cli_reference_includes_root_and_subcommands() -> None:
    text = build_cli_reference_text()
    assert "opensre" in text.lower()
    assert "--help" in text or "help" in text
    assert "investigate" in text.lower() or "agent" in text.lower()
