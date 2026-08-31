"""Tests for inline raw-terminal choice menu rendering."""

from __future__ import annotations

import io
import re
import sys
from types import SimpleNamespace

import pytest

from surfaces.shared.terminal.components import choice_menu

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
    assert "\r  integrations" in plain
    assert "\r  /integrations" in plain
    assert "\r  ❯ 1. /integrations list" in plain
    assert "\r    2. /integrations verify" in plain
    # Airy layout: a blank row separates the question block from the options,
    # and there is no full-width rule.
    assert "\r  /integrations\r\n\r\n\r  ❯ 1." in plain
    assert "─" not in plain


def test_draw_menu_letter_keys_labels_options_alphabetically(monkeypatch) -> None:
    # The clarification (Ask User) picker labels options (A)/(B)/(C) and its hint
    # advertises the letter-key range instead of digits.
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(choice_menu, "_cols", lambda: 80)

    choice_menu._draw_menu(
        title="How should I select repos?",
        crumb="",
        labels=["Local repos", "GitHub account", "Or type your own answer..."],
        index=0,
        erase_lines=0,
        header="Ask User",
        letter_keys=True,
    )

    plain = _ANSI_RE.sub("", out.getvalue())
    assert "❯ (A) Local repos" in plain
    assert "(B) GitHub account" in plain
    assert "(C) Or type your own answer..." in plain
    assert "Enter/A-C Select" in plain
    assert "1." not in plain


def test_pick_letter_key_selects_matching_option(monkeypatch) -> None:
    # Pressing the option's letter returns that option's index (case-insensitive).
    out = io.StringIO()
    actions = iter(["b"])
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(choice_menu, "_cols", lambda: 80)
    monkeypatch.setattr(choice_menu, "_read_action", lambda **_kwargs: next(actions))
    monkeypatch.setattr(choice_menu, "repl_tty_interactive", lambda: True)

    result = choice_menu._pick(
        title="Q",
        crumb="",
        labels=["Local", "GitHub", "Other"],
        letter_keys=True,
    )

    assert result == 1


def test_pick_letter_keys_still_navigates_with_arrows(monkeypatch) -> None:
    # Requirement: letter menus stay navigable by up/down arrows, not only keys.
    out = io.StringIO()
    actions = iter(["down", "down", "enter"])
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(choice_menu, "_cols", lambda: 80)
    monkeypatch.setattr(choice_menu, "_read_action", lambda **_kwargs: next(actions))
    monkeypatch.setattr(choice_menu, "repl_tty_interactive", lambda: True)

    result = choice_menu._pick(
        title="Q",
        crumb="",
        labels=["Local", "GitHub", "Other"],
        letter_keys=True,
    )

    assert result == 2


def test_draw_menu_multi_select_checkboxes(monkeypatch) -> None:
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(choice_menu, "_cols", lambda: 80)

    choice_menu._draw_menu(
        title="Extras",
        crumb="",
        labels=["Unit tests", "Dockerfile"],
        index=0,
        erase_lines=0,
        multi_select=True,
        checked={1},
    )

    plain = _ANSI_RE.sub("", out.getvalue())
    assert "[ ] 1. Unit tests" in plain
    assert "[x] 2. Dockerfile" in plain
    assert "Space/Enter/1-9 Toggle" in plain
    assert "Submit" in plain


def test_draw_menu_multi_select_letter_keys_labels_options(monkeypatch) -> None:
    # Multi-select still advertises A-…; the checkbox row must show the letter.
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(choice_menu, "_cols", lambda: 80)

    choice_menu._draw_menu(
        title="Extras",
        crumb="",
        labels=["Unit tests", "Dockerfile", "Or type your own answer..."],
        index=0,
        erase_lines=0,
        multi_select=True,
        checked={0},
        letter_keys=True,
    )

    plain = _ANSI_RE.sub("", out.getvalue())
    assert "[x] (A) Unit tests" in plain
    assert "[ ] (B) Dockerfile" in plain
    assert "[ ] (C) Or type your own answer..." in plain
    assert "Space/Enter/A-C Toggle" in plain
    assert "1." not in plain


def test_pick_multi_select_returns_values_not_labels(monkeypatch) -> None:
    """Checked rows must emit choice values even when labels differ."""
    out = io.StringIO()
    actions = iter([" ", "down", " ", "down", "down", "enter"])
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(choice_menu, "_cols", lambda: 80)
    monkeypatch.setattr(
        "surfaces.shared.terminal.components.key_reader.read_menu_or_char",
        lambda **_kwargs: next(actions),
    )
    monkeypatch.setattr(choice_menu, "repl_tty_interactive", lambda: True)

    result = choice_menu._pick(
        title="Extras",
        crumb="",
        labels=["Unit tests", "Dockerfile", "Or type…"],
        multi_select=True,
        values=["tests", "docker", "custom"],
    )
    assert result == "tests\ndocker"


def test_draw_menu_strips_control_characters_from_title_and_labels(monkeypatch) -> None:
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(choice_menu, "_cols", lambda: 80)

    choice_menu._draw_menu(
        title="\x1b]0;pwn\x07integrations",
        crumb="\x1b]/integrations",
        labels=["\x07/integrations list", "/integrations verify"],
        index=0,
        erase_lines=0,
    )

    rendered = out.getvalue()
    assert "\x1b]" not in rendered
    assert "\x07" not in rendered
    plain = _ANSI_RE.sub("", rendered)
    assert "integrations" in plain
    assert "/integrations list" in plain


def test_print_valid_choice_list_strips_controls_and_escapes_markup() -> None:
    from rich.console import Console

    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=80)
    choice_menu.print_valid_choice_list(
        console,
        title="\x1b]0;pwn\x07Pick [one]",
        choices=["\x07alpha [bold]"],
    )
    output = buffer.getvalue()
    assert "\x1b]" not in output
    assert "\x07" not in output
    assert "Pick [one]" in output
    assert "alpha [bold]" in output
    assert "[bold]" in output


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


def test_leave_inline_menu_starts_next_line_at_column_zero(monkeypatch) -> None:
    """After a padded menu, Rich output must not inherit a mid-line cursor."""
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(choice_menu, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(
        "surfaces.shared.terminal.components.key_reader.restore_stdin_terminal",
        lambda: None,
    )

    choice_menu.leave_inline_menu()

    # prepare_repl_output_line writes \\r\\n then reset_tty_column writes \\r
    assert "\r\n" in out.getvalue()
    assert out.getvalue().endswith("\r")


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


def test_repl_choose_one_starts_at_initial_value(monkeypatch) -> None:
    out = io.StringIO()
    actions = iter(["enter"])
    monkeypatch.setattr(choice_menu, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(
        "surfaces.shared.terminal.components.cpr_stdin.drain_stale_cpr_bytes",
        lambda: None,
    )
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(choice_menu, "_cols", lambda: 80)
    monkeypatch.setattr(choice_menu, "_read_action", lambda: next(actions))

    result = choice_menu.repl_choose_one(
        title="theme",
        breadcrumb="/theme",
        choices=[("green", "green"), ("blue", "blue (current)"), ("pink", "pink")],
        initial_value="blue",
    )

    assert result == "blue"
    plain = _ANSI_RE.sub("", out.getvalue())
    assert "❯ 2. blue (current)" in plain


def test_repl_choose_one_restores_terminal_when_menu_raises(monkeypatch) -> None:
    """Exceptions during draw/read still recook stdin and reset the column."""
    restored: list[bool] = []
    out = io.StringIO()

    def _restore() -> None:
        restored.append(True)

    def _boom(**_kwargs: object) -> None:
        raise RuntimeError("menu failed")

    monkeypatch.setattr(choice_menu, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(
        "surfaces.shared.terminal.components.cpr_stdin.drain_stale_cpr_bytes",
        lambda: None,
    )
    monkeypatch.setattr(
        "surfaces.shared.terminal.components.key_reader.restore_stdin_terminal",
        _restore,
    )
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(choice_menu, "_pick", _boom)

    with pytest.raises(RuntimeError, match="menu failed"):
        choice_menu.repl_choose_one(title="theme", choices=[("green", "green")])

    assert restored == [True]
    assert "\r\n" in out.getvalue()
    assert out.getvalue().endswith("\r")


def test_repl_choose_one_restores_terminal_once_on_success(monkeypatch) -> None:
    restored: list[bool] = []

    def _restore() -> None:
        restored.append(True)

    monkeypatch.setattr(choice_menu, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(
        "surfaces.shared.terminal.components.cpr_stdin.drain_stale_cpr_bytes",
        lambda: None,
    )
    monkeypatch.setattr(
        "surfaces.shared.terminal.components.key_reader.restore_stdin_terminal",
        _restore,
    )
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(choice_menu, "_cols", lambda: 80)
    monkeypatch.setattr(choice_menu, "_read_action", lambda: "enter")

    result = choice_menu.repl_choose_one(
        title="theme",
        choices=[("green", "green")],
    )

    assert result == "green"
    assert restored == [True]


def test_read_action_ignores_left_arrow(monkeypatch) -> None:
    keys = iter([b"\xe0", b"K"])
    monkeypatch.setattr(choice_menu.os, "name", "nt")
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(getch=lambda: next(keys)))

    assert choice_menu._read_action() == "ignore"
