"""Pure rendering tests for the ``/fleet`` dashboard table (issue #1488).

These cover ``render_agents_table`` in isolation — no slash-command
dispatch, no real registry I/O. The integration tests in
``test_agents_commands.py`` cover the dispatch path that consumes
this function.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from rich.console import Console
from rich.table import Table

from app.cli.interactive_shell.ui import agents_view as agents_view_mod
from app.cli.interactive_shell.ui.agents_view import _build_agents_table
from app.fleet_monitoring import config as config_mod
from app.fleet_monitoring.probe import ProcessSnapshot
from app.fleet_monitoring.registry import AgentRecord
from app.fleet_monitoring.token_rate import TOKEN_RATE_TRACKER


@pytest.fixture(autouse=True)
def isolated_agents_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Autouse: redirect ``agents_config_path`` to a per-test tmp file
    so the rendering tests don't read the developer's real
    ``~/.opensre/agents.yaml`` (which would let real budgets
    leak into the placeholder assertions and create cross-machine
    flakes).
    """
    target = tmp_path / "agents.yaml"
    monkeypatch.setattr(config_mod, "agents_config_path", lambda: target)
    return target


@pytest.fixture(autouse=True)
def _clear_sampler_module_state() -> None:
    """Reset module-level state in the sampler before each test.

    Three globals can leak across test files:

    - :data:`app.fleet_monitoring.sampler._latest` (probe snapshots dict)
    - :data:`app.fleet_monitoring.token_rate.TOKEN_RATE_TRACKER` (per-PID deque)
    - :data:`app.fleet_monitoring.sampler._TickCache.registry_snapshot` and
      :data:`_TickCache.agents_config` (tick caches added in #2023 to
      avoid re-reading the disk on every render)

    Sampler tests populate all of them; view tests must start clean
    so placeholder assertions are stable under ``pytest-xdist`` and
    in arbitrary alphabetical order.
    """
    from app.fleet_monitoring import sampler as sampler_mod

    sampler_mod._latest.clear()
    sampler_mod._TickCache.registry_snapshot = {}
    sampler_mod._TickCache.agents_config = None
    for pid in list(TOKEN_RATE_TRACKER.known_pids()):
        TOKEN_RATE_TRACKER.forget(pid)


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
    table = _build_agents_table(records)
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
    """Empty-state UX: the table caption tells the user the fleet
    is empty rather than leaving a blank table that looks like a bug.
    """
    _, out = _render([])
    assert "no agents discovered or registered yet" in out


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


def test_metric_cells_are_placeholders_when_no_sampler_data() -> None:
    """All five metric cells render ``-`` when the sampler has no
    data for the PID (REPL not running, non-interactive ``opensre
    agents list``, or fresh registration that has not yet been
    sampled). #2023 split ``tokens/min`` and ``$/hr`` from the
    yaml budget; both still fall back to ``-`` here for the same
    "no data" reason.
    """
    table, _ = _render([AgentRecord(name="claude-code", pid=8421, command="claude")])
    # row_count == 1, so iterate directly to inspect the rendered cells
    assert table.row_count == 1
    rendered_cells = [list(col.cells)[0] for col in table.columns]
    # cells[0] = agent, cells[1] = pid, then metric cells and status.
    assert rendered_cells[2:] == ["-", "-", "-", "-", "-"]


def test_table_shows_live_probe_data_when_snapshot_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed_now = datetime(2026, 5, 10, 14, 0, 0, tzinfo=UTC)
    started_at = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)  # exactly 2h before

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, _tz=None):  # type: ignore[override]
            return fixed_now

    monkeypatch.setattr(agents_view_mod, "datetime", FrozenDatetime)

    fake_snapshot = ProcessSnapshot(
        pid=8421,
        cpu_percent=23.5,
        rss_mb=128.0,
        num_fds=42,
        num_connections=3,
        status="running",
        started_at=started_at,
    )
    monkeypatch.setattr(agents_view_mod, "get_snapshot", lambda _pid: fake_snapshot)

    table, _ = _render([AgentRecord(name="cursor", pid=8444, command="cursor")])

    rendered_cells = [list(col.cells)[0] for col in table.columns]
    # ``tokens/min`` and ``$/hr`` are still ``-`` because no token
    # tracker entry exists for this PID — the snapshot fixture covers
    # only the resource side. The full live-data case is covered by
    # ``test_table_shows_tokens_and_cost_when_tracker_has_data``.
    # Without recent output activity, the status heuristic falls back
    # to the process start time and renders STUCK with a progress-time annotation.
    assert rendered_cells[2:] == ["2h0m", "23.5", "-", "-", "[red]stuck (2h0m no progress)[/red]"]


def test_recent_agent_output_keeps_status_active(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed_now = datetime(2026, 5, 10, 14, 0, 0, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, _tz=None):  # type: ignore[override]
            return fixed_now

    monkeypatch.setattr(agents_view_mod, "datetime", FrozenDatetime)

    fake_snapshot = ProcessSnapshot(
        pid=8421,
        cpu_percent=23.5,
        rss_mb=128.0,
        num_fds=42,
        num_connections=3,
        status="running",
        started_at=fixed_now - timedelta(hours=2),
        last_output_at=fixed_now - timedelta(seconds=30),
    )
    monkeypatch.setattr(agents_view_mod, "get_snapshot", lambda _pid: fake_snapshot)

    table, _ = _render([AgentRecord(name="cursor", pid=8444, command="cursor")])
    rendered_cells = [list(col.cells)[0] for col in table.columns]

    assert rendered_cells[6] == "[green]active[/green]"


def test_stuck_message_anchors_to_last_output_at_not_started_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_now = datetime(2026, 5, 10, 14, 0, 0, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, _tz=None):  # type: ignore[override]
            return fixed_now

    monkeypatch.setattr(agents_view_mod, "datetime", FrozenDatetime)

    fake_snapshot = ProcessSnapshot(
        pid=8421,
        cpu_percent=5.0,
        rss_mb=64.0,
        num_fds=10,
        num_connections=1,
        status="running",
        started_at=fixed_now - timedelta(hours=3),
        last_output_at=fixed_now - timedelta(minutes=10),
    )
    monkeypatch.setattr(agents_view_mod, "get_snapshot", lambda _pid: fake_snapshot)

    table, _ = _render([AgentRecord(name="cursor", pid=8444, command="cursor")])
    rendered_cells = [list(col.cells)[0] for col in table.columns]

    assert rendered_cells[6] == "[red]stuck (10m no progress)[/red]"


def test_table_shows_tokens_and_cost_when_tracker_has_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end of the #2023 wiring on the view side: when the
    sampler's accessors return live numbers, the view formats them
    into the ``tokens/min`` and ``$/hr`` columns.
    """
    monkeypatch.setattr(agents_view_mod, "get_tokens_per_min", lambda _pid: 120.0)
    monkeypatch.setattr(agents_view_mod, "get_usd_per_hour", lambda _pid: 0.27)

    table, _ = _render([AgentRecord(name="claude-code-8421", pid=8421, command="claude")])

    rendered_cells = [list(col.cells)[0] for col in table.columns]
    # cells[4] = tokens/min, cells[5] = $/hr
    assert rendered_cells[4] == "120"
    assert rendered_cells[5] == "$0.27"


def test_tokens_per_min_formatter_uses_k_suffix_above_1000(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A busy session emitting > 1k tokens/min must not blow out the
    column width; the formatter falls back to ``1.2k`` shorthand.
    """
    monkeypatch.setattr(agents_view_mod, "get_tokens_per_min", lambda _pid: 1234.0)
    monkeypatch.setattr(agents_view_mod, "get_usd_per_hour", lambda _pid: None)

    table, _ = _render([AgentRecord(name="claude-code", pid=8421, command="claude")])
    rendered_cells = [list(col.cells)[0] for col in table.columns]
    assert rendered_cells[4] == "1.2k"


def test_idle_observed_agent_renders_zero_tokens_per_min_not_dash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``0`` vs ``-`` distinction the tracker enforces must
    propagate to the cell: a known-but-idle agent renders ``0``,
    a never-observed agent renders ``-``.
    """
    monkeypatch.setattr(agents_view_mod, "get_tokens_per_min", lambda _pid: 0.0)
    monkeypatch.setattr(agents_view_mod, "get_usd_per_hour", lambda _pid: 0.0)

    table, _ = _render([AgentRecord(name="claude-code", pid=8421, command="claude")])
    rendered_cells = [list(col.cells)[0] for col in table.columns]
    assert rendered_cells[4] == "0"
    assert rendered_cells[5] == "$0.00"


def test_usd_per_hour_renders_dash_when_model_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``get_usd_per_hour`` returns ``None`` when the model cannot
    be resolved (and therefore pricing cannot be applied). The cell
    must render ``-`` rather than ``$0.00`` so the user does not
    misread "unknown" as "free".
    """
    monkeypatch.setattr(agents_view_mod, "get_tokens_per_min", lambda _pid: 500.0)
    monkeypatch.setattr(agents_view_mod, "get_usd_per_hour", lambda _pid: None)

    table, _ = _render([AgentRecord(name="claude-code", pid=8421, command="claude")])
    rendered_cells = [list(col.cells)[0] for col in table.columns]
    # tokens/min still shows the live figure …
    assert rendered_cells[4] == "500"
    # … but $/hr is honest about the missing rate.
    assert rendered_cells[5] == "-"


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


# Resilience to a schema-invalid ``agents.yaml`` is now enforced by
# the sampler's catch-all around ``load_agents_config`` (see
# ``test_sampler.py``); the view no longer touches the config.
