"""Rendering the investigation context above the feedback prompt."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from surfaces.shared.terminal.feedback.prompts import _write_raw

if TYPE_CHECKING:
    from rich.console import Console


def _format_root_cause_lines(root: str, *, cols: int) -> list[str]:
    """Wrap root-cause text to terminal width with a hanging ``Root cause:`` prefix."""
    import textwrap

    prefix = "Root cause: "
    content_width = max(20, cols - len(prefix))
    wrapped = textwrap.wrap(root, width=content_width)
    if not wrapped:
        return []
    lines = [prefix + wrapped[0]]
    indent = " " * len(prefix)
    lines.extend(indent + line for line in wrapped[1:])
    return lines


def _root_cause_width(*, console: Console | None) -> int:
    """Best-effort terminal width for root-cause display (matches REPL tables)."""
    import shutil

    from surfaces.shared.terminal.components.rendering import _repl_table_width

    if console is not None:
        return _repl_table_width(console)
    return max(40, shutil.get_terminal_size(fallback=(80, 24)).columns)


def _print_context(final_state: dict[str, Any], *, console: Console | None) -> None:
    """Print the root-cause summary above the rating prompt."""
    root = (final_state.get("root_cause") or "").strip()
    if not root:
        return

    cols = _root_cause_width(console=console)

    from rich.markup import escape

    from infrastructure.terminal.theme import BRAND, DIM, SECONDARY

    if console is not None:
        console.print()
        console.rule(characters="─", style=DIM)
        console.print(
            f"[{SECONDARY}]Root cause:[/] [{BRAND}]{escape(root)}[/]",
            soft_wrap=True,
            width=cols,
        )
    else:
        rule = "─" * cols
        body = "\n".join(_format_root_cause_lines(root, cols=cols))
        _write_raw(f"\n{rule}\n{body}\n{rule}\n")
