"""Tests for /tools slash command pager behavior."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from io import StringIO

import pytest
from rich.console import Console

from surfaces.interactive_shell.command_registry import tools_cmds
from surfaces.interactive_shell.ui.tables.tool_catalog import ToolCatalogEntry


class _FakeSession:
    def mark_latest(self, *, ok: bool, kind: str) -> None:  # pragma: no cover - unused
        pass


def _entry(name: str) -> ToolCatalogEntry:
    return ToolCatalogEntry(
        name=name,
        surfaces=("investigation",),
        description=f"Description for {name}.",
        source_file=f"tools/{name}.py",
        input_schema_summary="",
    )


def _catalog(size: int) -> list[ToolCatalogEntry]:
    return [_entry(f"tool_{i}") for i in range(size)]


# ------------------------------------------------------------------ pager decision


def test_should_page_returns_false_when_not_terminal() -> None:
    assert tools_cmds._should_page(500, 24, is_terminal=False) is False


def test_should_page_returns_false_when_terminal_height_unknown() -> None:
    assert tools_cmds._should_page(500, 0, is_terminal=True) is False


def test_should_page_returns_false_when_output_fits() -> None:
    # 3 entries × 3 rows/entry + 4 chrome = 13 < 40
    assert tools_cmds._should_page(3, 40, is_terminal=True) is False


def test_should_page_returns_true_when_output_taller_than_terminal() -> None:
    # 30 entries × 3 rows + 4 chrome = 94 > 24
    assert tools_cmds._should_page(30, 24, is_terminal=True) is True


def test_estimate_table_height_is_monotonic() -> None:
    assert tools_cmds._estimate_table_height(0) < tools_cmds._estimate_table_height(1)
    assert tools_cmds._estimate_table_height(1) < tools_cmds._estimate_table_height(10)


# ---------------------------------------------------------------------- rendering


def _make_console(*, tty: bool, height: int) -> Console:
    console = Console(file=StringIO(), width=200, force_terminal=tty, no_color=True, height=height)
    # Rich >= 13 exposes is_terminal from force_terminal; belt-and-braces guard.
    assert bool(console.is_terminal) is tty
    return console


def test_short_catalog_renders_inline_no_pager(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools_cmds, "build_tool_catalog", lambda: _catalog(3))
    pager_used = False

    @contextmanager
    def _fake_pager(*_args: object, **_kwargs: object) -> Iterator[None]:
        nonlocal pager_used
        pager_used = True
        yield

    console = _make_console(tty=True, height=40)
    monkeypatch.setattr(console, "pager", _fake_pager)

    assert tools_cmds._list_tools(_FakeSession(), console, []) is True  # type: ignore[arg-type]
    assert pager_used is False
    output: str = console.file.getvalue()  # type: ignore[attr-defined]
    for entry in ("tool_0", "tool_1", "tool_2"):
        assert entry in output


def test_long_catalog_wraps_render_in_pager(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools_cmds, "build_tool_catalog", lambda: _catalog(40))
    pager_used = False

    @contextmanager
    def _fake_pager(*_args: object, **_kwargs: object) -> Iterator[None]:
        nonlocal pager_used
        pager_used = True
        yield

    console = _make_console(tty=True, height=24)
    monkeypatch.setattr(console, "pager", _fake_pager)

    assert tools_cmds._list_tools(_FakeSession(), console, []) is True  # type: ignore[arg-type]
    assert pager_used is True


def test_non_tty_never_pages_even_for_long_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools_cmds, "build_tool_catalog", lambda: _catalog(100))
    pager_used = False

    @contextmanager
    def _fake_pager(*_args: object, **_kwargs: object) -> Iterator[None]:
        nonlocal pager_used
        pager_used = True
        yield

    console = _make_console(tty=False, height=24)
    monkeypatch.setattr(console, "pager", _fake_pager)

    assert tools_cmds._list_tools(_FakeSession(), console, []) is True  # type: ignore[arg-type]
    assert pager_used is False
    output: str = console.file.getvalue()  # type: ignore[attr-defined]
    assert "tool_0" in output
    assert "tool_99" in output


def test_empty_catalog_renders_hint_no_pager(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools_cmds, "build_tool_catalog", lambda: [])
    pager_used = False

    @contextmanager
    def _fake_pager(*_args: object, **_kwargs: object) -> Iterator[None]:
        nonlocal pager_used
        pager_used = True
        yield

    console = _make_console(tty=True, height=24)
    monkeypatch.setattr(console, "pager", _fake_pager)

    assert tools_cmds._list_tools(_FakeSession(), console, []) is True  # type: ignore[arg-type]
    assert pager_used is False
    # Existing "no tools registered." hint is delegated to render_tools_table.


# ------------------------------------------------------------------------ command


def test_command_surface_unchanged() -> None:
    (spec,) = tools_cmds.COMMANDS
    assert spec.name == "/tools"
    assert spec.usage == ("/tools", "/tools list")
    first_arg_names = {name for name, _desc in spec.first_arg_completions or ()}
    assert first_arg_names == {"list", "ls", "tool", "tools"}
