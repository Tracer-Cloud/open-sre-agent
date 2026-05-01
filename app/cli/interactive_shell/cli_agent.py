"""LangGraph-free assistant for the interactive terminal - CLI guidance and chat."""

from __future__ import annotations

from typing import Literal

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape

from app.cli.interactive_shell.cli_reference import build_cli_reference_text
from app.cli.interactive_shell.loaders import llm_loader
from app.cli.interactive_shell.session import ReplSession

# Cap stored (user, assistant) pairs; list holds 2 entries per turn.
_MAX_CLI_AGENT_TURNS = 12
type _GroundingMode = Literal["reference_only", "conversational"]

# Shared, end-user-friendly terminology rule that is appended to every system
# prompt. The model otherwise picks up "REPL" from internal docs and surfaces
# jargon to the user (#604).
_TERMINOLOGY_RULE = (
    "Terminology: always call this surface the 'interactive shell' (the "
    "OpenSRE interactive terminal launched via `opensre` or `opensre agent`). "
    "Never use the word 'REPL' in user-facing answers - it is internal jargon."
)

_MARKDOWN_RULE = (
    "Formatting: respond in concise Markdown. Markdown will be rendered "
    "in the user's terminal, so tables, **bold**, lists, and `code spans` "
    "will display correctly - do not wrap the whole answer in a code fence."
)


def _format_history_for_prompt(session: ReplSession) -> str:
    """Render recent CLI agent turns for multi-turn context."""
    lines: list[str] = []
    cap = _MAX_CLI_AGENT_TURNS * 2
    for role, content in session.cli_agent_messages[-cap:]:
        label = "User" if role == "user" else "Assistant"
        lines.append(f"{label}: {content}")
    return "\n".join(lines) if lines else "(no prior messages in this CLI thread)"


def _build_system_prompt(grounding: _GroundingMode, reference: str, history: str) -> str:
    """Build the system prompt for one assistant turn.

    Split out so tests can assert on terminology / formatting rules without
    invoking an LLM.
    """
    if grounding == "reference_only":
        return (
            "You are the OpenSRE CLI assistant. The user is in the OpenSRE "
            "interactive shell (the `opensre` terminal) or asking how to use "
            "OpenSRE from the shell.\n"
            "Answer ONLY using the reference below. If the reference does not "
            "cover their question, say so briefly and suggest `opensre --help` "
            "or `/help` inside the interactive shell. Prefer copy-pastable "
            "commands. Keep the answer concise.\n\n"
            f"{_TERMINOLOGY_RULE}\n{_MARKDOWN_RULE}\n\n"
            f"--- Reference ---\n{reference}\n"
        )
    return (
        "You are the OpenSRE terminal assistant. You help with OpenSRE CLI "
        "usage, the interactive shell, and onboarding - you do NOT run "
        "incident investigations yourself (those use a separate LangGraph "
        "pipeline).\n"
        "When the user wants to investigate an alert, tell them to paste "
        "alert text, JSON, or a concrete incident description (errors, "
        "services, symptoms). Mention `opensre investigate` and pasting "
        "into this interactive shell.\n"
        "Be brief and friendly. Ground CLI facts in the reference below; do "
        "not invent subcommands.\n\n"
        f"{_TERMINOLOGY_RULE}\n{_MARKDOWN_RULE}\n\n"
        f"--- CLI reference ---\n{reference}\n\n"
        f"--- Recent CLI conversation ---\n{history}\n"
    )


def answer_cli_agent(
    message: str,
    session: ReplSession,
    console: Console,
    *,
    grounding: _GroundingMode = "conversational",
) -> None:
    """Run one turn of the terminal assistant (no LangGraph / no investigation pipeline).

    Use ``grounding="reference_only"`` for strict procedural CLI Q&A (same as
    :func:`answer_cli_help`).
    """
    try:
        from app.services.llm_client import get_llm_for_reasoning
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]LLM client unavailable:[/red] {escape(str(exc))}")
        return

    reference = build_cli_reference_text()
    history = _format_history_for_prompt(session)
    system = _build_system_prompt(grounding, reference, history)
    user_block = (
        f"--- Question ---\n{message}"
        if grounding == "reference_only"
        else f"--- User message ---\n{message}"
    )
    prompt = f"{system}\n{user_block}"

    try:
        with llm_loader(console):
            client = get_llm_for_reasoning()
            response = client.invoke(prompt)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]assistant failed:[/red] {escape(str(exc))}")
        return

    text = getattr(response, "content", None) or str(response)
    text_str = str(text)
    session.cli_agent_messages.append(("user", message))
    session.cli_agent_messages.append(("assistant", text_str))
    cap = _MAX_CLI_AGENT_TURNS * 2
    if len(session.cli_agent_messages) > cap:
        session.cli_agent_messages[:] = session.cli_agent_messages[-cap:]

    console.print()
    console.print("[bold cyan]assistant:[/bold cyan]")
    # Render the answer as Markdown so tables, bold, lists, and code spans
    # display correctly in the terminal instead of leaking raw `**bold**`,
    # `| col |` table syntax, etc. (#604).
    console.print(Markdown(text_str))
    console.print()


__all__ = ["answer_cli_agent"]
