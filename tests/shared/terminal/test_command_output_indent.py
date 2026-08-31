"""Command output is indented under its ``$ command`` header when it fits."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from surfaces.shared.terminal.tables.tables import print_command_output


def _out(text: str, *, width: int) -> str:
    console = Console(file=StringIO(), force_terminal=False, width=width)
    print_command_output(console, text)
    return console.file.getvalue()


def test_short_result_is_indented_under_the_header_with_a_marker() -> None:
    out = _out("done", width=80)
    assert "↳ done" in out  # parent → child hierarchy


def test_multiline_result_that_fits_indents_with_one_marker() -> None:
    out = _out("first\nsecond", width=80)
    assert "↳ first" in out
    assert "second" in out
    assert out.count("↳") == 1  # marker on the first line only


def test_wide_output_stays_flush_so_it_does_not_wrap() -> None:
    out = _out("x" * 40, width=20)
    assert "↳" not in out  # too wide to indent — left flush
