"""Markdown block and response-header rendering shared by the streamed and unstreamed reply paths."""

from __future__ import annotations

import re
import sys
from typing import TYPE_CHECKING

from rich.console import Console

import infrastructure.terminal.theme as ui_theme
from core.agent_harness.spi.prompt_chrome import normalize_three_tier_spacing
from core.agent_harness.spi.session_goal import strip_session_goal_progress_tags
from infrastructure.safety.terminal_output import strip_terminal_controls

if TYPE_CHECKING:
    from rich.markdown import Markdown

STREAM_LABEL_ASSISTANT = "assistant"
STREAM_LABEL_ANSWER = "answer"

# Rich Markdown treats ``__init__.py`` as bold emphasis around ``init``, which
# strips the underscores and restyles that span. Escape dunder filenames so
# path-heavy reports (architecture audits, etc.) keep uniform body color.
_DUNDER_FILENAME_RE = re.compile(r"__([A-Za-z0-9_]+)__(?=\.py\b)")


def _escape_markdown_dunder_filenames(text: str) -> str:
    """Neutralize ``__name__.py`` so Markdown does not parse it as strong emphasis."""
    return _DUNDER_FILENAME_RE.sub(r"\_\_\1\_\_", text)


def _build_markdown_block(text: str) -> Markdown:
    """Build a Markdown renderable with the shared escaping and code theme.

    Strips terminal controls (ESC/CR/BEL/C1) while keeping LF/Tab so multi-line
    model prose cannot spoof the TTY. All whole and streamed markdown paths
    build through this helper.

    Reads the ``Markdown`` class off the already-loaded package module via
    ``sys.modules`` rather than importing it here (directly, or by importing
    the package back) — tests substitute the class by patching
    ``surfaces.interactive_shell.ui.streaming.Markdown``, and any import
    binding in this module would bind a copy that patch never reaches. A
    ``sys.modules`` lookup carries no static import edge back to the package,
    so it does not create the back-edge an ``import`` statement here would.
    """
    assert __package__  # always set for a package submodule
    package = sys.modules[__package__]
    safe = strip_terminal_controls(text, keep_whitespace=True)
    spaced = normalize_three_tier_spacing(safe)
    return package.Markdown(  # type: ignore[no-any-return]
        _escape_markdown_dunder_filenames(spaced.rstrip()),
        code_theme=ui_theme.MARKDOWN_CODE_THEME,
    )


def render_markdown_block(console: Console, text: str) -> None:
    """Render one complete Markdown block using the shared markdown theme.

    The single rendering path for model prose that arrives whole (not
    chunk-streamed) — e.g. the action agent's intermediate phase headers —
    so every markdown surface shares one escaping/theme policy. Terminal
    controls are stripped inside ``_build_markdown_block``.
    """
    visible = strip_session_goal_progress_tags(text)
    if not visible.strip():
        return
    with console.use_theme(ui_theme.MARKDOWN_THEME):
        console.print(_build_markdown_block(visible))


def render_response_header(console: Console, label: str) -> None:
    """Print the ``●`` bullet row marker that opens every assistant
    response (Claude Code-style row layout). Shared with
    ``action_turn.run_action_tool_turn`` so the planned-actions path
    and the streaming response path use the exact same prefix.
    """
    console.print(f"[{ui_theme.BOLD_BRAND}]●[/] [{ui_theme.DIM}]{label}[/]")
