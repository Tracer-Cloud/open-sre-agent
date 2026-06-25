"""Tests for post-investigation feedback UI."""

from __future__ import annotations

import io
import os

import pytest
from rich.console import Console

from cli.interactive_shell.ui.feedback import (
    _CHOICES,
    _format_root_cause_lines,
    _print_context,
    _root_cause_width,
    _run_select,
)


def _fixed_terminal_size(*_args: object, **_kwargs: object) -> os.terminal_size:
    return os.terminal_size((60, 24))


def _wide_terminal_size(*_args: object, **_kwargs: object) -> os.terminal_size:
    return os.terminal_size((160, 24))


def test_root_cause_width_uses_full_terminal_not_capped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("shutil.get_terminal_size", _wide_terminal_size)

    assert _root_cause_width(console=None) == 160


def test_print_context_stdout_uses_full_terminal_width(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("shutil.get_terminal_size", _wide_terminal_size)
    root = "Schema validation failed because payment_method is missing."

    _print_context({"root_cause": root}, console=None)

    captured = capsys.readouterr().out
    assert captured.startswith("\n" + "─" * 160 + "\n")
    assert captured.rstrip().endswith("─" * 160)


def test_format_root_cause_lines_wraps_long_text_without_truncation() -> None:
    root = (
        "The Kubernetes job 'etl-transform-error' for pipeline "
        "'kubernetes_etl_pipeline' failed in namespace 'tracer-test' because "
        "schema validation requires 'payment_method'."
    )

    lines = _format_root_cause_lines(root, cols=60)

    assert len(lines) > 1
    assert all("…" not in line for line in lines)
    assert " ".join(line.strip() for line in lines) == f"Root cause: {root}"


def test_print_context_shows_full_root_cause_in_rich_path() -> None:
    root = (
        "The Kubernetes job 'etl-transform-error' for pipeline "
        "'kubernetes_etl_pipeline' failed because schema validation requires "
        "'payment_method'."
    )
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=60)

    _print_context({"root_cause": root}, console=console)

    output = buf.getvalue()
    assert root in output
    assert "…" not in output


def test_print_context_escapes_rich_markup_in_root() -> None:
    root = "Pod restart [1/3] failed because schema validation requires 'payment_method'."
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=80)

    _print_context({"root_cause": root}, console=console)

    output = buf.getvalue()
    assert "[1/3]" in output
    assert "…" not in output


def test_print_context_shows_full_root_cause_in_stdout_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("shutil.get_terminal_size", _fixed_terminal_size)
    root = (
        "The Kubernetes job 'etl-transform-error' for pipeline "
        "'kubernetes_etl_pipeline' failed because schema validation requires "
        "'payment_method'."
    )

    _print_context({"root_cause": root}, console=None)

    captured = capsys.readouterr().out
    assert "…" not in captured
    assert " ".join(captured.split()) == ("─" * 60 + " Root cause: " + root + " " + "─" * 60)


def test_run_select_returns_highlighted_choice_on_enter(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    calls = {"n": 0}

    def read_then_enter(**_kwargs: object) -> str:
        calls["n"] += 1
        return "enter" if calls["n"] > 1 else "down"

    monkeypatch.setattr(
        "cli.interactive_shell.ui.feedback.read_key_unix",
        read_then_enter,
    )
    monkeypatch.setattr("cli.interactive_shell.ui.feedback.flush_stdin_unix", lambda: None)
    monkeypatch.setattr(
        "cli.interactive_shell.ui.feedback.restore_stdin_terminal",
        lambda: None,
    )

    assert _run_select(_CHOICES) == "partial"
    captured = capsys.readouterr().out
    assert "Partially accurate" in captured
    assert "↑↓" in captured
