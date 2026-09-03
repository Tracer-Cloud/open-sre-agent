"""The action log groups consecutive same-kind calls into collapsible sections."""

from __future__ import annotations

import io

from rich.console import Console

from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.session.terminal_session import ActionLogEntry
from surfaces.interactive_shell.ui.action_log import flush_action_log


def _tty(buffer: io.StringIO) -> Console:
    return Console(file=buffer, force_terminal=True, highlight=False, color_system="truecolor")


def _push(session: Session, call_id: str, kind: str, concise: str, detail: str) -> None:
    session.terminal.push_action_log(
        ActionLogEntry(call_id=call_id, kind=kind, concise=concise, detail=detail)
    )


def test_consecutive_same_kind_calls_group_into_one_bordered_section() -> None:
    session = Session()
    _push(session, "1", "GitHub CLI", "gh repo view", "⏺ GitHub CLI · gh repo view")
    _push(session, "2", "GitHub CLI", "gh pr list", "⏺ GitHub CLI · gh pr list")
    buffer = io.StringIO()

    flush_action_log(_tty(buffer), session)

    out = buffer.getvalue()
    assert "GitHub CLI · 2 actions" in out  # one section header with the count
    assert all(corner in out for corner in ("╭", "╮", "╰", "╯"))  # full box border
    assert "gh repo view" in out  # concise rows, no dotted args
    assert "gh pr list" in out
    assert "Ctrl+O to expand details" in out
    assert session.terminal.has_action_log() is False  # buffer drained
    # Full detail is reachable via Ctrl+O.
    assert "gh repo view" in session.terminal.next_collapsed_output_for_expand()


def test_no_inline_dotted_arguments_on_a_single_call() -> None:
    session = Session()
    # A generic tool: concise is empty (label only), args live in the detail.
    _push(
        session,
        "1",
        "list github actions workflow runs",
        "",
        "⏺ list github actions workflow runs\n    owner: Tracer-Cloud\n    per_page: 100",
    )
    buffer = io.StringIO()

    flush_action_log(_tty(buffer), session)

    out = buffer.getvalue()
    assert "list github actions workflow runs" in out
    assert " · " not in out  # no dotted argument strip on the visible line
    assert "per_page" not in out  # args are hidden behind Ctrl+O


def test_a_lone_call_is_a_dim_line_not_a_one_row_box() -> None:
    session = Session()
    _push(session, "1", "GitHub CLI", "gh pr list", "⏺ GitHub CLI · gh pr list")
    buffer = io.StringIO()

    flush_action_log(_tty(buffer), session)

    out = buffer.getvalue()
    assert "gh pr list" in out
    assert not any(corner in out for corner in ("╭", "╮", "╰", "╯"))  # no box for one call


def test_two_different_lone_kinds_render_as_two_dim_lines_no_box() -> None:
    session = Session()
    _push(session, "1", "GitHub CLI", "gh pr list", "d1")
    _push(session, "2", "opensre", "opensre cron list", "d2")
    buffer = io.StringIO()

    flush_action_log(_tty(buffer), session)

    out = buffer.getvalue()
    assert out.count("╭") == 0  # neither group reaches 2 same-kind calls
    assert "gh pr list" in out
    assert "opensre cron list" in out


def test_non_tty_inlines_the_detail() -> None:
    session = Session()
    _push(session, "1", "GitHub CLI", "gh pr list", "⏺ GitHub CLI · gh pr list\n  ↳ 4 open PRs")
    buffer = io.StringIO()

    flush_action_log(Console(file=buffer, force_terminal=False, highlight=False), session)

    out = buffer.getvalue()
    assert "gh pr list" in out
    assert "4 open PRs" in out
    assert "Ctrl+O" not in out
