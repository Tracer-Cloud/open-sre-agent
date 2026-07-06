"""Tests for the /tools slash command router."""

from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console

from surfaces.interactive_shell.command_registry import tools_cmds
from surfaces.interactive_shell.ui.tables.tool_catalog import ToolCatalogEntry


class _FakeSession:
    """Minimal Session stand-in matching the slash-handler contract."""

    def __init__(self) -> None:
        self.marks: list[tuple[bool, str]] = []

    def mark_latest(self, *, ok: bool, kind: str) -> None:
        self.marks.append((ok, kind))


def _entry(name: str, description: str, surfaces: tuple[str, ...]) -> ToolCatalogEntry:
    return ToolCatalogEntry(
        name=name,
        surfaces=surfaces,
        description=description,
        source_file=f"tools/{name}.py",
        input_schema_summary="",
    )


@pytest.fixture
def catalog(monkeypatch: pytest.MonkeyPatch) -> list[ToolCatalogEntry]:
    entries = [
        _entry("query_datadog_metric", "Query Datadog metrics.", ("investigation",)),
        _entry("slack_send_message", "Send a Slack message.", ("chat",)),
        _entry("get_sre_guidance", "Retrieve SRE guidance snippets.", ("investigation", "chat")),
    ]
    monkeypatch.setattr(tools_cmds, "build_tool_catalog", lambda: list(entries))
    return entries


def _run(args: list[str]) -> tuple[_FakeSession, str]:
    session = _FakeSession()
    console = Console(file=StringIO(), width=200, force_terminal=False, no_color=True)
    handled = tools_cmds._cmd_tools(session, console, args)  # type: ignore[arg-type]
    assert handled is True
    output: str = console.file.getvalue()  # type: ignore[attr-defined]
    return session, output


def test_filter_catalog_substring_case_insensitive(catalog: list[ToolCatalogEntry]) -> None:
    filtered = tools_cmds._filter_catalog(catalog, "SLACK")
    assert [e.name for e in filtered] == ["slack_send_message"]


def test_filter_catalog_matches_description(catalog: list[ToolCatalogEntry]) -> None:
    filtered = tools_cmds._filter_catalog(catalog, "guidance")
    assert [e.name for e in filtered] == ["get_sre_guidance"]


def test_filter_catalog_empty_query_returns_all(catalog: list[ToolCatalogEntry]) -> None:
    assert tools_cmds._filter_catalog(catalog, "   ") == catalog


def test_bare_command_lists_all(catalog: list[ToolCatalogEntry]) -> None:
    _, output = _run([])
    for entry in catalog:
        assert entry.name in output


def test_list_alias_delegates(catalog: list[ToolCatalogEntry]) -> None:
    for alias in ("list", "ls", "tool", "tools"):
        _, output = _run([alias])
        assert "query_datadog_metric" in output


def test_search_requires_query(catalog: list[ToolCatalogEntry]) -> None:
    session, output = _run(["search"])
    assert "usage:" in output
    assert session.marks == [(False, "slash")]


def test_search_narrows_output(catalog: list[ToolCatalogEntry]) -> None:
    _, output = _run(["search", "slack"])
    assert "slack_send_message" in output
    assert "query_datadog_metric" not in output


def test_bare_query_treated_as_filter(catalog: list[ToolCatalogEntry]) -> None:
    _, output = _run(["datadog"])
    assert "query_datadog_metric" in output
    assert "slack_send_message" not in output


def test_search_no_matches_prints_hint(catalog: list[ToolCatalogEntry]) -> None:
    session, output = _run(["search", "nonexistent"])
    assert "no tools match" in output
    assert "'nonexistent'" in output
    assert session.marks == [(True, "slash")]


def test_count_shows_total_and_per_surface(catalog: list[ToolCatalogEntry]) -> None:
    _, output = _run(["count"])
    assert "3 registered tools" in output
    assert "investigation" in output
    assert "chat" in output
    # per-surface totals reflect double-counting when a tool is on both surfaces
    assert "investigation" in output and "2" in output
    assert "chat" in output and "2" in output


def test_count_singular_form(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tools_cmds,
        "build_tool_catalog",
        lambda: [_entry("solo", "Only tool.", ("chat",))],
    )
    _, output = _run(["count"])
    assert "1 registered tool" in output


def test_count_empty_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools_cmds, "build_tool_catalog", lambda: [])
    _, output = _run(["count"])
    assert "no tools registered" in output


def test_command_metadata_lists_new_subcommands() -> None:
    (spec,) = tools_cmds.COMMANDS
    assert spec.name == "/tools"
    assert "/tools count" in spec.usage
    assert "/tools search <query>" in spec.usage
    first_arg_names = {name for name, _desc in spec.first_arg_completions or ()}
    assert {"list", "ls", "count", "search"} <= first_arg_names


def test_first_arg_completions_expose_new_verbs() -> None:
    args = dict(tools_cmds._TOOLS_FIRST_ARGS)
    assert "count" in args
    assert "search" in args


def test_bare_command_still_works_without_args(catalog: list[ToolCatalogEntry]) -> None:
    # `_cmd_tools` should handle both `args=None` semantics and empty list.
    session = _FakeSession()
    console = Console(file=StringIO(), width=200, force_terminal=False, no_color=True)
    assert tools_cmds._cmd_tools(session, console, []) is True  # type: ignore[arg-type]


def test_matches_returns_false_on_miss(catalog: list[ToolCatalogEntry]) -> None:
    entry = catalog[0]
    assert tools_cmds._matches(entry, "definitely_missing_token") is False
