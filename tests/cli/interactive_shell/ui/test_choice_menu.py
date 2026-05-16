"""Tests for inline raw-terminal choice menu rendering."""

from __future__ import annotations

import io
import re
import sys

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
