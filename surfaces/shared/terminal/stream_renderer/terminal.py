"""Terminal print helpers for streamed investigation rendering."""

from __future__ import annotations

import sys

from surfaces.shared.terminal.output import get_output_format
from surfaces.shared.terminal.stream_renderer.constants import _BOLD, _CYAN, _DIM, _RESET


def _print_connection_banner() -> None:
    if get_output_format() == "rich":
        sys.stdout.write(
            f"\n  {_BOLD}{_CYAN}Remote Investigation{_RESET}"
            f"  {_DIM}streaming from deployed agent{_RESET}\n\n"
        )
    else:
        print("\n  Remote Investigation  streaming from deployed agent\n")
    sys.stdout.flush()


def _print_info(message: str) -> None:
    if get_output_format() == "rich":
        sys.stdout.write(f"\n  {_DIM}{message}{_RESET}\n")
    else:
        print(f"\n  {message}")
    sys.stdout.flush()
