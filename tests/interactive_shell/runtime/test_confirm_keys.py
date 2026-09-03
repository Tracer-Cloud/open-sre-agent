"""The single-keypress confirmation resolver maps a key to a row answer."""

from __future__ import annotations

import pytest

from surfaces.interactive_shell.runtime.core.confirm_keys import resolve_confirm_answer

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
