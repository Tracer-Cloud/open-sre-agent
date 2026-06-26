"""Terminal print helpers for streamed investigation rendering."""

from __future__ import annotations

import sys
from typing import Any

from cli.interactive_shell.ui.output import get_output_format
from cli.interactive_shell.ui.theme import BRAND
from cli.ui.renderer.constants import _BOLD, _CYAN, _DIM, _RESET


def _print_connection_banner() -> None:
    if get_output_format() == "rich":
        sys.stdout.write(
            f"\n  {_BOLD}{_CYAN}Remote Investigation{_RESET}"
            f"  {_DIM}streaming from deployed agent{_RESET}\n\n"
        )
    else:
        print("\n  Remote Investigation  streaming from deployed agent\n")
    sys.stdout.flush()


def _print_section(title: str, content: str, console: Any | None = None) -> None:
    if get_output_format() == "rich":
        from rich.console import Console
        from rich.markdown import Markdown
        from rich.padding import Padding
        from rich.rule import Rule

        from cli.interactive_shell.ui.theme import MARKDOWN_THEME

        c = console or Console(highlight=False)
        c.print()
        c.print(Rule(f"[bold] {title} [/]", style=BRAND, align="left"))
        with c.use_theme(MARKDOWN_THEME):
            c.print(Padding(Markdown(content.strip(), code_theme="ansi_dark"), (1, 2)))
    else:
        print(f"\n  {title}")
        for line in content.strip().splitlines():
            print(f"  {line}")
    sys.stdout.flush()


def _print_info(message: str) -> None:
    if get_output_format() == "rich":
        sys.stdout.write(f"\n  {_DIM}{message}{_RESET}\n")
    else:
        print(f"\n  {message}")
    sys.stdout.flush()
