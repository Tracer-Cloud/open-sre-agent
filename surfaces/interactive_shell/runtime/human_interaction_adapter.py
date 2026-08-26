"""Prompt-mediated human hand-offs for the interactive shell."""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from core.agent_harness.ports import (
    ConfirmFn,
    HumanInteractionPort,
    UserChoiceRequest,
)
from infrastructure.safety.terminal_output import strip_terminal_controls
from infrastructure.terminal.theme import DIM, HIGHLIGHT
from surfaces.shared.terminal.components.choice_menu import repl_tty_interactive

_CHOICE_PROMPT = "Choose a number or option label, or type another answer:"


class ReplHumanInteractionPort:
    """Render a choice and wait through the REPL's prompt-owned input path."""

    def __init__(
        self,
        console: Console,
        confirm_fn: ConfirmFn,
    ) -> None:
        self._console = console
        self._confirm_fn = confirm_fn

    def choose(self, request: UserChoiceRequest) -> str | None:
        title = Text()
        title.append(f"{strip_terminal_controls(request.header)}: ", style=HIGHLIGHT)
        title.append(strip_terminal_controls(request.question))
        self._console.print()
        self._console.print(title)
        for index, option in enumerate(request.options, start=1):
            line = Text(f"  {index}. {strip_terminal_controls(option.label)}")
            line.append(f" — {strip_terminal_controls(option.description)}", style=DIM)
            self._console.print(line)
        self._console.print(Text("  Other — type a custom answer", style=DIM))

        answer = self._confirm_fn(_CHOICE_PROMPT).strip()
        if not answer:
            return None
        if answer.isdecimal():
            index = int(answer) - 1
            if 0 <= index < len(request.options):
                return request.options[index].label
        normalized = answer.casefold()
        for option in request.options:
            if option.label.casefold() == normalized:
                return option.label
        return answer


def repl_human_interaction_factory(
    console: Console,
    confirm_fn: ConfirmFn | None,
    is_tty: bool | None,
) -> HumanInteractionPort | None:
    """Build the REPL interaction port only when prompt input is available."""
    if confirm_fn is None or is_tty is False:
        return None
    if is_tty is None and not repl_tty_interactive():
        return None
    return ReplHumanInteractionPort(console, confirm_fn)


__all__ = ["ReplHumanInteractionPort", "repl_human_interaction_factory"]
