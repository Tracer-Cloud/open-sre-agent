"""Tests for ReplSubprocessPresenter Rich markup escaping."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from surfaces.interactive_shell.runtime.subprocess_runner.repl_presenter import (
    ReplSubprocessPresenter,
    _escape_markup_message,
)
from surfaces.interactive_shell.session import Session


def _presenter() -> tuple[ReplSubprocessPresenter, StringIO]:
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=True, width=80, color_system=None)
    session = Session()
    return ReplSubprocessPresenter(session, console), buffer


def test_escape_markup_message_preserves_intentional_tags() -> None:
    escaped = _escape_markup_message("[error]failed[/]")
    buffer = StringIO()
    Console(file=buffer, force_terminal=True, width=80, color_system=None).print(escaped)
    output = buffer.getvalue()
    assert "failed" in output


def test_escape_markup_message_escapes_plain_markup() -> None:
    escaped = _escape_markup_message("Grafana [prod] rate[5m]")
    buffer = StringIO()
    Console(file=buffer, force_terminal=True, width=80, color_system=None).print(escaped)
    output = buffer.getvalue()
    assert "[prod]" in output
    assert "rate[5m]" in output


def test_escape_markup_message_escapes_dynamic_text_inside_tags() -> None:
    escaped = _escape_markup_message("[bold]task [critical][/bold]")
    assert "task" in escaped
    assert "[critical]" in escaped


def test_print_error_escapes_dynamic_markup() -> None:
    presenter, buffer = _presenter()
    presenter.print_error("failed: [bold]bad[/]")
    output = buffer.getvalue()
    assert "[bold]bad[/]" in output
    assert "bad" in output


def test_print_escapes_untrusted_suffix_via_print_error_pattern() -> None:
    presenter, buffer = _presenter()
    presenter.print_error("command failed to start: [bold]bad[/]")
    output = buffer.getvalue()
    assert "[bold]bad[/]" in output


def test_print_preserves_task_id_markup_with_escaped_brackets() -> None:
    presenter, buffer = _presenter()
    task_id = "task-[critical]"
    presenter.print(
        f"[dim]synthetic test started — task[/] [bold]{task_id}[/bold]. "
        "[highlight]/tasks[/] [dim]to monitor.[/]"
    )
    output = buffer.getvalue()
    assert task_id in output
    assert "/tasks" in output
