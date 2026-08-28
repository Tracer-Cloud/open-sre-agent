"""When the prompt app is suspended (subprocess turns), confirmation reads a
plain line instead of parking on the hidden arrow-nav — otherwise it hangs while
the cooked terminal echoes the arrow keys.
"""

from __future__ import annotations

import builtins

import pytest

from surfaces.interactive_shell.runtime.turn_host import _confirm_via_readline

_THREE = (
    ("y", "Yes, allow"),
    ("always", "Yes, and always allow reversible commands"),
    ("n", "No, cancel"),
)


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("a", "y"),  # row tag
        ("2", "always"),  # digit
        ("n", "n"),  # answer key
        ("y", "y"),
        ("weird", "weird"),  # passthrough for the gate to interpret
    ],
)
def test_readline_maps_tags_digits_and_answers(monkeypatch, typed: str, expected: str) -> None:
    monkeypatch.setattr(builtins, "input", lambda _prompt: typed)
    assert _confirm_via_readline("Approve?", _THREE) == expected


def test_readline_treats_interrupt_as_cancel(monkeypatch) -> None:
    def _raise(_prompt: str) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(builtins, "input", _raise)
    assert _confirm_via_readline("Approve?", _THREE) == "n"


def test_readline_defaults_to_yes_no_without_options(monkeypatch) -> None:
    captured: list[str] = []
    monkeypatch.setattr(builtins, "input", lambda prompt: captured.append(prompt) or "y")
    assert _confirm_via_readline("Approve?", None) == "y"
    # Two default rows -> a two-tag prompt.
    assert captured == ["Approve? [a/b] "]
