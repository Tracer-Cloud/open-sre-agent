"""The single-keypress confirmation resolver maps a key to a row answer."""

from __future__ import annotations

import pytest

from surfaces.interactive_shell.runtime.core.confirm_keys import (
    _drain_escape_tail,
    resolve_confirm_answer,
)

_ROWS = (
    ("y", "Yes, allow"),
    ("always", "Yes, and always allow reversible commands"),
    ("n", "No, cancel"),
)


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("a", "y"),  # row tag
        ("b", "always"),  # row tag maps to the multi-char answer key
        ("2", "always"),  # 1-based digit
        ("n", "n"),  # the row's own answer key
        ("", "n"),  # Enter → arrow-nav default (last row = cancel)
        ("  ", "n"),  # whitespace-only counts as Enter
    ],
)
def test_resolve_maps_tags_digits_answers_and_enter(key: str, expected: str) -> None:
    assert resolve_confirm_answer(key, _ROWS) == expected


@pytest.mark.parametrize("key", ["z", "9", "\x1b", "q"])
def test_resolve_ignores_unknown_keys(key: str) -> None:
    # The raw-key reader depends on None so it swallows arrows/junk and keeps
    # waiting instead of echoing garbage or resolving a wrong answer.
    assert resolve_confirm_answer(key, _ROWS) is None


def test_drain_escape_tail_does_not_read_when_no_bytes_are_pending() -> None:
    """Standalone Escape: do not block waiting for a CSI tail that will never come."""
    reads: list[str] = []

    def _read() -> str:
        reads.append("blocked")
        return "["

    assert _drain_escape_tail(_read, has_input=lambda _timeout: False) == ""
    assert reads == []


def test_drain_escape_tail_consumes_a_complete_arrow_sequence() -> None:
    stream = list("[A")
    waits: list[float] = []

    def _read() -> str:
        return stream.pop(0) if stream else ""

    def _pending(timeout: float) -> bool:
        waits.append(timeout)
        return bool(stream)

    assert _drain_escape_tail(_read, has_input=_pending) == ""
    assert stream == []
    assert waits[0] > 0  # first wait may pause for the CSI introducer
    assert all(wait == 0 for wait in waits[1:])


def test_drain_escape_tail_stops_on_an_incomplete_csi_without_eating_the_next_choice() -> None:
    # ESC [ arrived, but the final arrow byte did not. The operator then types
    # 'a' (Yes). That choice must stay unread.
    stream = list("[")
    leftover = ["a"]

    def _read() -> str:
        if stream:
            return stream.pop(0)
        return leftover.pop(0)

    def _pending(timeout: float) -> bool:
        return bool(stream)

    assert _drain_escape_tail(_read, has_input=_pending) == ""
    assert leftover == ["a"]


@pytest.mark.parametrize("choice", ["a", "b", "y", "n"])
def test_drain_escape_tail_returns_a_pending_choice_after_incomplete_csi(choice: str) -> None:
    """ESC [ and a choice letter are already buffered; the letter is not a CSI final."""
    stream = list(f"[{choice}")

    def _read() -> str:
        return stream.pop(0) if stream else ""

    def _pending(_timeout: float) -> bool:
        return bool(stream)

    leftover = _drain_escape_tail(_read, has_input=_pending)
    assert leftover == choice
    assert stream == []


@pytest.mark.parametrize(
    ("tail", "leftover"),
    [
        ("Oa", "O"),  # SS3 prefix, then a choice — do not invent a CSI
        ("a", "a"),  # lone ESC then a choice typed within the introducer wait
        ("[1;5A", ""),  # Ctrl+Up: parameters + keyboard final
        ("[3~", ""),  # Delete
    ],
)
def test_drain_escape_tail_returns_only_non_csi_leftovers(tail: str, leftover: str) -> None:
    stream = list(tail)

    def _read() -> str:
        return stream.pop(0) if stream else ""

    def _pending(_timeout: float) -> bool:
        return bool(stream)

    assert _drain_escape_tail(_read, has_input=_pending) == leftover
