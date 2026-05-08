"""Tests for REPL inline menu TTY detection."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.cli.interactive_shell import repl_choice_menu as rcm


def test_repl_tty_interactive_falls_back_to_stdio_fds(monkeypatch: object) -> None:
    """Prompt-toolkit-style stdio wrappers may lie about isatty; fds 0/1 are authoritative."""
    mock_in = MagicMock()
    mock_in.isatty.return_value = False
    mock_out = MagicMock()
    mock_out.isatty.return_value = False
    monkeypatch.setattr(rcm.sys, "stdin", mock_in)
    monkeypatch.setattr(rcm.sys, "stdout", mock_out)

    def _isatty(fd: int) -> bool:
        return fd in (0, 1)

    monkeypatch.setattr(rcm.os, "isatty", _isatty)
    assert rcm.repl_tty_interactive() is True


def test_repl_tty_interactive_false_when_no_tty(monkeypatch: object) -> None:
    mock_in = MagicMock()
    mock_in.isatty.return_value = False
    mock_out = MagicMock()
    mock_out.isatty.return_value = False
    monkeypatch.setattr(rcm.sys, "stdin", mock_in)
    monkeypatch.setattr(rcm.sys, "stdout", mock_out)
    monkeypatch.setattr(rcm.os, "isatty", lambda _fd: False)
    assert rcm.repl_tty_interactive() is False
