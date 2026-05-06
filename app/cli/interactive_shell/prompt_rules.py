"""Shared LLM prompt rules for interactive-shell assistants."""

from __future__ import annotations

# Align copy across docs-aware and conversational CLI assistants so wording
# does not drift between modules.
INTERACTIVE_SHELL_TERMINOLOGY_RULE = (
    "Terminology: always call this surface the 'interactive shell' (the "
    "OpenSRE interactive terminal launched via `opensre` or `opensre agent`). "
    "Never use the word 'REPL' in user-facing answers - it is internal jargon."
)

CLI_ASSISTANT_MARKDOWN_RULE = (
    "Formatting: respond in concise Markdown. Markdown will be rendered "
    "in the user's terminal, so tables, **bold**, lists, and `code spans` "
    "will display correctly - do not wrap the whole answer in a code fence."
)

__all__ = [
    "CLI_ASSISTANT_MARKDOWN_RULE",
    "INTERACTIVE_SHELL_TERMINOLOGY_RULE",
]
