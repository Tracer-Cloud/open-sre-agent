"""Shared LLM prompt rules for interactive-shell assistants."""

from __future__ import annotations

# Align copy across docs-aware and conversational CLI assistants so wording
# does not drift between modules.
INTERACTIVE_SHELL_TERMINOLOGY_RULE = (
    "Terminology: always call this surface the 'interactive shell' (the "
    "OpenSRE interactive terminal launched when you run `opensre` from an "
    "interactive terminal). Never use the word 'REPL' in user-facing answers "
    "- it is internal jargon."
)

CLI_ASSISTANT_MARKDOWN_RULE = (
    "Formatting: respond in concise Markdown. Markdown will be rendered "
    "in the user's terminal, so tables, **bold**, lists, and `code spans` "
    "will display correctly - do not wrap the whole answer in a code fence."
)

AGENT_RESPONSE_THREE_TIER_RULE = (
    "Response shape: when you report findings (especially after tool results), "
    "use three parts when the answer is more than a one-line status:\n"
    "1. **I found:** — the fact or conclusion in plain language.\n"
    "2. **Here's what that looks like:** — a short structured view (list, table, "
    "or code block) when it helps the user scan the data; omit this part for "
    "trivial answers.\n"
    "3. **Want me to:** — one specific next step tied to the finding (not a "
    "generic 'let me know if you need anything'). After integration status "
    "questions, offer something concrete such as connecting another "
    "integration, verifying a failed one, or running setup for a missing "
    "service.\n"
    "For single-line confirmations, keep the main answer to one sentence, but "
    "still add **Want me to:** when a sensible follow-up exists."
)


def format_agent_response(
    found: str,
    display: str = "",
    next_action: str = "",
) -> str:
    """Format assistant findings as the standard three-tier Markdown block.

    ``found`` is required when ``display`` or ``next_action`` is supplied.
    """
    finding = found.strip()
    detail = display.strip()
    offer = next_action.strip()
    if not finding:
        if detail or offer:
            raise ValueError("found is required when display or next_action is set")
        return ""
    if not detail and not offer:
        return finding
    sections = [f"**I found:** {finding}"]
    if detail:
        sections.append(f"**Here's what that looks like:**\n{detail}")
    if offer:
        sections.append(f"**Want me to:** {offer}")
    return "\n\n".join(sections)


__all__ = [
    "AGENT_RESPONSE_THREE_TIER_RULE",
    "CLI_ASSISTANT_MARKDOWN_RULE",
    "INTERACTIVE_SHELL_TERMINOLOGY_RULE",
    "format_agent_response",
]
