"""Tests for interactive-shell investigation action helpers."""

from __future__ import annotations

from tools.interactive_shell.actions.investigation import normalize_investigation_alert_text


def test_normalize_investigation_alert_text_strips_outer_double_quotes() -> None:
    assert normalize_investigation_alert_text('"hello world"') == "hello world"


def test_normalize_investigation_alert_text_strips_outer_single_quotes() -> None:
    assert normalize_investigation_alert_text("'hello world'") == "hello world"


def test_normalize_investigation_alert_text_leaves_unquoted_text() -> None:
    assert normalize_investigation_alert_text("hello world") == "hello world"
