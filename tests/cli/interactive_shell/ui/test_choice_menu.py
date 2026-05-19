"""Tests for inline raw-terminal choice menu rendering."""

from __future__ import annotations

import io
import re
import sys
from types import SimpleNamespace

from app.cli.interactive_shell.ui import choice_menu

_ANSI_RE = re.compile(r"\x1b\[[0-9;:]*[A-Za-z]")


def test_draw_menu_uses_carriage_return_newlines(monkeypatch) -> None:
    """Raw-mode terminals do not translate LF to CRLF for us.

    Plain ``\n`` makes each line begin at the previous line's ending column,
    which renders the picker as a diagonal staircase. The inline menu should
    write explicit ``\r\n`` newlines and reset to column zero for every row.
    """
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(choice_menu, "_cols", lambda: 80)

    choice_menu._draw_menu(
        title="integrations",
        crumb="/integrations",
        labels=["/integrations list", "/integrations verify"],
        index=0,
        erase_lines=0,
    )

    rendered = out.getvalue()
    plain = _ANSI_RE.sub("", rendered)
    assert "\n" in rendered
    assert all(rendered[index - 1] == "\r" for index, char in enumerate(rendered) if char == "\n")
    assert "\rintegrations" in plain
    assert "\r/integrations" in plain
    assert "\r > /integrations list" in plain


def test_erase_menu_block_resets_to_column_zero(monkeypatch) -> None:
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)

    choice_menu._erase_menu("crumb", ["one", "two"])

    rendered = out.getvalue()
    assert rendered.startswith("\r\x1b[")
    assert "A\r\x1b[J" in rendered
    assert rendered.endswith("\r")


def test_reset_tty_column_writes_carriage_return(monkeypatch) -> None:
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)

    choice_menu.reset_tty_column()

    assert out.getvalue() == "\r"


def test_pick_ignores_unmapped_keys(monkeypatch) -> None:
    out = io.StringIO()
    actions = iter(["ignore", "enter"])
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(choice_menu, "_cols", lambda: 80)
    monkeypatch.setattr(choice_menu, "_read_action", lambda: next(actions))

    assert choice_menu._pick(title="test", crumb="", labels=["one"]) == 0

    rendered = out.getvalue()
    assert rendered.count("test") == 2
    assert "A\r\x1b[J" in rendered


def test_read_action_treats_space_as_enter(monkeypatch) -> None:
    monkeypatch.setattr(choice_menu.os, "name", "nt")
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(getch=lambda: b" "))

    assert choice_menu._read_action() == "enter"


def test_read_action_treats_right_arrow_as_enter(monkeypatch) -> None:
    keys = iter([b"\xe0", b"M"])
    monkeypatch.setattr(choice_menu.os, "name", "nt")
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(getch=lambda: next(keys)))

    assert choice_menu._read_action() == "enter"


def test_read_action_ignores_left_arrow(monkeypatch) -> None:
    keys = iter([b"\xe0", b"K"])
    monkeypatch.setattr(choice_menu.os, "name", "nt")
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(getch=lambda: next(keys)))

    assert choice_menu._read_action() == "ignore"
