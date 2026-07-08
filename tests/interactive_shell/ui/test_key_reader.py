"""Tests for terminal cooked-mode restore after raw-mode menus."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from surfaces.interactive_shell.ui.components import key_reader


def test_restore_stdin_terminal_recooks_input_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    # A raw menu (tty.setraw) clears ICRNL, so Enter (CR) stops submitting until it
    # is restored. Pin that restore re-enables the cooked-mode flags on a raw snapshot.
    termios = pytest.importorskip("termios")

    monkeypatch.setattr(key_reader.os, "name", "posix")
    monkeypatch.setattr(
        key_reader.sys, "stdin", SimpleNamespace(isatty=lambda: True, fileno=lambda: 0)
    )

    raw_attrs = [0, 0, 0, 0, 0, 0, []]  # iflag/oflag/cflag/lflag all cleared (raw mode)
    written: dict[str, list[object]] = {}
    monkeypatch.setattr(termios, "tcgetattr", lambda _fd: list(raw_attrs))
    monkeypatch.setattr(
        termios, "tcsetattr", lambda _fd, _when, attrs: written.__setitem__("attrs", attrs)
    )
    monkeypatch.setattr(termios, "tcflush", lambda _fd, _queue: None)

    key_reader.restore_stdin_terminal()

    attrs = written["attrs"]
    assert attrs[0] & termios.ICRNL  # CR -> NL so Enter submits again
    assert attrs[1] & termios.OPOST  # output newline post-processing
    assert attrs[3] & termios.ICANON  # line editing
    assert attrs[3] & termios.ECHO  # keystrokes visible


def test_restore_stdin_terminal_is_a_no_op_when_not_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(key_reader.os, "name", "posix")
    monkeypatch.setattr(
        key_reader.sys,
        "stdin",
        SimpleNamespace(isatty=lambda: False, fileno=lambda: (_ for _ in ()).throw(AssertionError)),
    )
    key_reader.restore_stdin_terminal()  # returns without touching termios
