"""Pure rendering tests for the ``/agents`` dashboard table (issue #1488).

These cover ``render_agents_table`` in isolation — no slash-command
dispatch, no real registry I/O. The integration tests in
``test_agents_commands.py`` cover the dispatch path that consumes
this function.
"""

from __future__ import annotations

import io

from rich.console import Console
from rich.table import Table

from app.agents.registry import AgentRecord
from app.cli.interactive_shell.agents_view import render_agents_table

# The columns this PR ships are the contract for #1490 and later
# tickets that thread snapshot data into the rendering layer; pin
# them here so a downstream reorder doesn't silently break the
# dashboard preview.
_DASHBOARD_COLUMNS: tuple[str, ...] = (
    "agent",
    "pid",
    "uptime",
    "cpu%",
    "tokens/min",
    "$/hr",
    "status",
)


def _render(records: list[AgentRecord]) -> tuple[Table, str]:
    """Build the table and capture the printed form for substring assertions."""
    table = render_agents_table(records)
    buf = io.StringIO()
    Console(file=buf, force_terminal=False, highlight=False, width=120).print(table)
    return table, buf.getvalue()


# ---------------------------------------------------------------------------
# Column structure — the contract downstream tickets lean on
# ---------------------------------------------------------------------------


def test_table_has_full_dashboard_column_set_in_documented_order() -> None:
    table, _ = _render([])
    headers = tuple(str(col.header) for col in table.columns)
    assert headers == _DASHBOARD_COLUMNS


def test_pid_column_is_right_justified_to_match_numeric_dashboard_preview() -> None:
    """Numeric columns (pid, uptime, cpu%, tokens/min, $/hr) are
    right-justified; agent name and status are left-justified. This
    matches the spacing in the issue's mock and keeps later
    snapshot-injected cells aligned without re-styling."""
    table, _ = _render([])
    by_header = {str(col.header): col for col in table.columns}
    assert by_header["pid"].justify == "right"
    assert by_header["uptime"].justify == "right"
    assert by_header["cpu%"].justify == "right"
    assert by_header["tokens/min"].justify == "right"
    assert by_header["$/hr"].justify == "right"
    assert by_header["agent"].justify == "left"
    assert by_header["status"].justify == "left"


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------


def test_empty_records_renders_table_with_zero_rows() -> None:
    table, _ = _render([])
    assert table.row_count == 0


def test_empty_records_caption_announces_empty_state() -> None:
    """Empty-state UX: the table caption tells the user the registry
    is empty rather than leaving a blank table that looks like a bug.

    The caption deliberately doesn't suggest a registration command —
    the ``register`` subcommand is currently a stub. When that gains
    real behavior, this caption can grow a hint pointing at it.
    """
    _, out = _render([])
    assert "no agents registered yet" in out


def test_non_empty_records_have_no_caption() -> None:
    """When the registry has rows, the caption is suppressed —
    the table content speaks for itself and a caption would be noise."""
    table, _ = _render([AgentRecord(name="claude-code", pid=8421, command="claude")])
    assert table.caption is None


# ---------------------------------------------------------------------------
# Row content
# ---------------------------------------------------------------------------


def test_row_contains_agent_name_and_pid() -> None:
    _, out = _render([AgentRecord(name="claude-code", pid=8421, command="claude")])
    assert "claude-code" in out
    assert "8421" in out


def test_metric_cells_are_placeholders_until_wired() -> None:
    """``uptime``, ``cpu%``, ``tokens/min``, ``$/hr``, ``status`` all
    render as ``-`` placeholders for now. The wiring in #1490 / #1494
    fills them; this PR only owns the column shape."""
    table, _ = _render([AgentRecord(name="claude-code", pid=8421, command="claude")])
    # row_count == 1, so iterate directly to inspect the rendered cells
    assert table.row_count == 1
    # The rendered output contains five dashes per row (one per metric column)
    rendered_cells = [list(col.cells)[0] for col in table.columns]
    # cells[0] = agent, cells[1] = pid, then 5 placeholder cells
    assert rendered_cells[2:] == ["-", "-", "-", "-", "-"]


def test_multiple_records_are_each_rendered_in_order() -> None:
    records = [
        AgentRecord(name="claude-code", pid=8421, command="claude"),
        AgentRecord(name="cursor-tab", pid=9133, command="cursor"),
        AgentRecord(name="aider", pid=7702, command="aider"),
    ]
    table, out = _render(records)
    assert table.row_count == 3
    # Substring order in the rendered output preserves input order:
    pos_claude = out.index("claude-code")
    pos_cursor = out.index("cursor-tab")
    pos_aider = out.index("aider")
    assert pos_claude < pos_cursor < pos_aider


# ---------------------------------------------------------------------------
# Defense against Rich-markup injection
# ---------------------------------------------------------------------------


def test_record_name_is_rich_escaped_so_markup_does_not_render() -> None:
    """An adversarial agent name containing Rich markup tags must
    render literally, not interpreted. Without ``escape()``, a name
    like ``[bold red]ghost[/]`` would visually mimic a styled cell
    and could mask other dashboard content."""
    records = [
        AgentRecord(name="[bold red]ghost[/]", pid=1, command="bin"),
    ]
    _, out = _render(records)
    # Literal brackets survive in the rendered output:
    assert "[bold red]ghost[/]" in out
