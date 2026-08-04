"""Tool output must never be parsed as terminal markup.

A skill body, shell stdout, or model reply can contain square brackets — the
morning-report recipe embeds ``sed -E 's/<title><!\\[CDATA\\[//; s/\\]\\]>//'``.
Printed through Rich with markup enabled, that reads as an unbalanced tag and
raises ``MarkupError``, which killed the whole turn with "turn error: closing
tag ... doesn't match any open tag" and no briefing.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from core.agent_harness.prompts.skills_loader import load_skill_body


def _console() -> tuple[Console, io.StringIO]:
    buffer = io.StringIO()
    return Console(file=buffer, force_terminal=False, highlight=False), buffer


def test_a_skill_body_is_not_valid_markup() -> None:
    """Pins why escaping is required, so the guard below is not mistaken for noise."""
    # Arrange
    console, _ = _console()

    # Act / Assert
    with pytest.raises(Exception, match="closing tag"):
        console.print(load_skill_body("morning-report"))


def test_the_action_driver_prints_tool_output_without_interpreting_markup() -> None:
    """The turn must survive output containing bracket-heavy shell commands."""
    # Arrange
    from core.agent_harness.turns.action_driver import _show_response

    console, buffer = _console()
    body = load_skill_body("morning-report")

    class _Sink:
        """Rich-backed sink with default settings — what the shell actually has."""

        def print(self, message: str = "") -> None:
            console.print(message)

        def render_response_header(self, label: str) -> None:
            console.print(label)

        def stream(self, *, label: str, chunks) -> str:  # noqa: ARG002 - protocol shape
            text = "".join(chunks)
            console.print(text)
            return text

    # Act
    _show_response(_Sink(), handled=False, final_text="", display_chunks=[body])

    # Assert
    assert "CDATA" in buffer.getvalue()
