"""Sound-notification gating and playback fallbacks."""

from __future__ import annotations

import io
import sys

import pytest

from infrastructure.terminal import notify
from infrastructure.terminal.notify import (
    NotifyEvent,
    play_notification,
    sound_enabled,
    terminal_is_focused,
)


def test_sound_is_disabled_unless_env_is_truthy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSRE_SOUND", raising=False)
    assert not sound_enabled()
    monkeypatch.setenv("OPENSRE_SOUND", "1")
    assert sound_enabled()
    monkeypatch.setenv("OPENSRE_SOUND", "false")
    assert not sound_enabled()


def test_play_is_a_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    # Disabled: no bell, no subprocess — the shell stays silent by default.
    monkeypatch.delenv("OPENSRE_SOUND", raising=False)
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    play_notification(NotifyEvent.TURN_COMPLETE)
    assert buf.getvalue() == ""


def test_non_darwin_falls_back_to_the_terminal_bell(monkeypatch: pytest.MonkeyPatch) -> None:
    # When enabled off macOS, focus is undeterminable so it chimes (bell fallback).
    monkeypatch.setenv("OPENSRE_SOUND", "1")
    monkeypatch.setattr(notify.platform, "system", lambda: "Linux")
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    play_notification(NotifyEvent.INPUT_NEEDED)
    assert buf.getvalue() == "\a"


def test_stays_silent_when_the_terminal_is_focused(monkeypatch: pytest.MonkeyPatch) -> None:
    # No chime while you are already looking at the terminal.
    monkeypatch.setenv("OPENSRE_SOUND", "1")
    monkeypatch.setattr(notify, "terminal_is_focused", lambda: True)
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    play_notification(NotifyEvent.TURN_COMPLETE)
    assert buf.getvalue() == ""


def test_chimes_when_the_terminal_is_unfocused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_SOUND", "1")
    monkeypatch.setattr(notify, "terminal_is_focused", lambda: False)
    monkeypatch.setattr(notify.platform, "system", lambda: "Linux")
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    play_notification(NotifyEvent.TURN_COMPLETE)
    assert buf.getvalue() == "\a"


def test_focus_matches_our_terminal_against_the_frontmost_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # macOS: our terminal (TERM_PROGRAM=vscode -> "Code") focused iff it is frontmost.
    monkeypatch.setattr(notify.platform, "system", lambda: "Darwin")
    monkeypatch.setenv("TERM_PROGRAM", "vscode")
    monkeypatch.setattr(notify, "_macos_frontmost_app", lambda: "Code")
    assert terminal_is_focused() is True
    monkeypatch.setattr(notify, "_macos_frontmost_app", lambda: "Safari")
    assert terminal_is_focused() is False
    monkeypatch.setattr(notify, "_macos_frontmost_app", lambda: None)
    assert terminal_is_focused() is None


def test_playback_errors_never_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    # A notification must never disrupt the turn — a failing player is swallowed.
    monkeypatch.setenv("OPENSRE_SOUND", "1")

    def _boom() -> str:
        raise RuntimeError("no audio device")

    monkeypatch.setattr(notify.platform, "system", _boom)
    play_notification(NotifyEvent.TURN_COMPLETE)  # must not raise
