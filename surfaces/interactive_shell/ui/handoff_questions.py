"""Style the user's answers to a structured hand-off (picker / Ask-User).

A turn is treated as a hand-off answer only when the harness flagged that a
picker or Ask-User handoff issued the question; the answer text then uses the
brand colour so it reads apart from the assistant's questions in the transcript.
"""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from core.agent_harness.spi.handoff import parse_ask_user_answers
from infrastructure.safety.terminal_output import strip_terminal_controls
from infrastructure.terminal import theme as ui_theme


def _display_safe(text: str) -> str:
    """Strip terminal controls while keeping newlines for multi-line questions."""
    return "\n".join(strip_terminal_controls(line) for line in text.splitlines())


def render_choice_selection(console: Console, title: str, answer: str) -> None:
    """Persist a compact selected-choice result after the transient picker closes.

    A multi-select answer arrives as one option per line; each line is indented
    under the heading so the selected set reads as one aligned block rather than
    a first row that hangs indented while the rest sit flush-left.
    """
    heading = Text()
    heading.append("✓ ", style=f"bold {ui_theme.HIGHLIGHT}")
    heading.append(_display_safe(title.strip()), style=str(ui_theme.TEXT))
    console.print(heading)
    for line in _display_safe(answer.strip()).splitlines():
        if not line.strip():
            continue
        row = Text("  ", style=str(ui_theme.DIM))
        row.append(line, style=str(ui_theme.BRAND))
        console.print(row)


def render_ask_user_qa(console: Console, pairs: list[tuple[str, str]]) -> None:
    """Print Ask User Q→A: accent header, bold numbered questions, brand answers.

    Each pair is a two-line block — a bold question, then its answer in the brand
    colour indented beneath it — with a blank row after the header and between
    items so the filled-in recap is scannable and the answer reads apart from the
    question. No extra blank above or below the card (the stream / prompt
    already own that margin).
    """
    console.print(Text("Ask User", style=f"bold {ui_theme.HIGHLIGHT}"))
    console.print()
    for index, (question, answer) in enumerate(pairs):
        if index:
            console.print()
        qline = Text()
        qline.append(f"  {index + 1}.  ", style=str(ui_theme.DIM))
        qline.append(_display_safe(question), style=f"bold {ui_theme.TEXT}")
        console.print(qline)
        aline = Text()
        aline.append("      ", style=str(ui_theme.DIM))
        aline.append(_display_safe(answer), style=str(ui_theme.BRAND))
        console.print(aline)


def try_render_ask_user_submission(console: Console, text: str) -> bool:
    """Render a batched Ask User answer block. True when the text matched."""
    pairs = parse_ask_user_answers(text)
    if len(pairs) < 2:
        return False
    render_ask_user_qa(console, pairs)
    return True


__all__ = [
    "render_ask_user_qa",
    "render_choice_selection",
    "try_render_ask_user_submission",
]
