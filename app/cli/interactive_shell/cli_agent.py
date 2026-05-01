"""LangGraph-free assistant for the interactive terminal - CLI guidance and chat."""

from __future__ import annotations

import json
from typing import Literal

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape

from app.cli.interactive_shell.cli_reference import build_cli_reference_text
from app.cli.interactive_shell.loaders import llm_loader
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.theme import TERMINAL_ACCENT_BOLD

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

_ACTION_RULE = (
    "Action planning: if the user asks you to change OpenSRE runtime state, "
    "return ONLY a compact JSON object with an `actions` array. Do not give "
    "instructions when an allowed action can satisfy the request. Allowed "
    "action object schemas: "
    "`{\"action\":\"switch_llm_provider\",\"provider\":\"anthropic\",\"model\":\"\"}` "
    "where provider is one of anthropic, openai, openrouter, gemini, nvidia, "
    "ollama, codex and model is optional; "
    "`{\"action\":\"slash\",\"command\":\"/model show\"}` where command is one of "
    "/model show, /list models, /health, /doctor, /version. For ordinary "
    "questions, return normal Markdown."
)

_ALLOWED_SLASH_ACTIONS = frozenset(
    {
        "/model show",
        "/list models",
        "/health",
        "/doctor",
        "/version",
    }
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
        "usage, the interactive shell, and onboarding. Explicit local shell "
        "commands are executed by a deterministic pre-pass before this LLM "
        "assistant is called; do not tell users the interactive shell cannot "
        "execute commands. You do NOT run incident investigations yourself "
        "(those use a separate LangGraph pipeline).\n"
        "When the user wants to investigate an alert, tell them to paste "
        "alert text, JSON, or a concrete incident description (errors, "
        "services, symptoms). Mention `opensre investigate` and pasting "
        "into this interactive shell.\n"
        "Be brief and friendly. Ground CLI facts in the reference below; do "
        "not invent subcommands.\n\n"
        f"{_TERMINOLOGY_RULE}\n{_MARKDOWN_RULE}\n{_ACTION_RULE}\n\n"
        f"--- CLI reference ---\n{reference}\n\n"
        f"--- Recent CLI conversation ---\n{history}\n"
    )


def _extract_json_object(text: str) -> dict[str, object] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].strip() == "```":
            stripped = "\n".join(lines[1:-1]).strip()

    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            payload, _end = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _normalize_action(action: dict[str, object]) -> dict[str, object] | None:
    normalized = dict(action)
    kind = str(normalized.get("action", "")).strip()
    if not kind and str(normalized.get("provider", "")).strip():
        normalized["action"] = "switch_llm_provider"
        return normalized
    if not kind and str(normalized.get("command", "")).strip():
        normalized["action"] = "slash"
        return normalized
    return normalized if kind else None


def _parse_action_plan(text: str) -> list[dict[str, object]]:
    payload = _extract_json_object(text)
    if payload is None:
        return []
    actions = payload.get("actions")
    if not isinstance(actions, list):
        normalized = _normalize_action(payload)
        return [normalized] if normalized is not None else []
    return [
        normalized
        for action in actions
        if isinstance(action, dict)
        for normalized in [_normalize_action(action)]
        if normalized is not None
    ]


def _execute_action_plan(
    actions: list[dict[str, object]],
    session: ReplSession,
    console: Console,
) -> bool:
    if not actions:
        return False

    from app.cli.interactive_shell.commands import dispatch_slash, switch_llm_provider

    console.print()
    console.print(f"[{TERMINAL_ACCENT_BOLD}]assistant:[/]")
    console.print("[dim]Requested actions:[/dim]")
    for index, action in enumerate(actions, start=1):
        kind = str(action.get("action", "")).strip()
        if kind == "switch_llm_provider":
            provider = str(action.get("provider", "")).strip()
            model = str(action.get("model", "")).strip()
            label = f"switch LLM provider to {provider}"
            if model:
                label += f" ({model})"
        elif kind == "slash":
            label = str(action.get("command", "")).strip()
        else:
            label = f"unsupported action: {kind or '?'}"
        console.print(f"[dim]{index}.[/dim] [{TERMINAL_ACCENT_BOLD}]{escape(label)}[/]")

    console.print()
    console.print("[dim]Running requested actions:[/dim]")
    for action in actions:
        kind = str(action.get("action", "")).strip()
        console.print()
        if kind == "switch_llm_provider":
            provider = str(action.get("provider", "")).strip()
            model = str(action.get("model", "")).strip() or None
            if not provider:
                console.print("[red]missing provider for switch_llm_provider action[/red]")
                continue
            console.print(f"[bold]$ /model set {escape(provider)}[/bold]")
            switch_llm_provider(provider, console, model=model)
            session.record("slash", f"/model set {provider}")
            continue

        if kind == "slash":
            command = str(action.get("command", "")).strip()
            if command not in _ALLOWED_SLASH_ACTIONS:
                console.print(f"[red]unsupported action command:[/red] {escape(command)}")
                continue
            session.record("slash", command)
            console.print(f"[bold]$ {escape(command)}[/bold]")
            dispatch_slash(command, session, console)
            continue

        console.print(f"[red]unsupported action:[/red] {escape(kind or '?')}")
    console.print()
    return True


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
    actions = _parse_action_plan(text_str)
    if _execute_action_plan(actions, session, console):
        session.cli_agent_messages.append(("user", message))
        session.cli_agent_messages.append(("assistant", text_str))
        cap = _MAX_CLI_AGENT_TURNS * 2
        if len(session.cli_agent_messages) > cap:
            session.cli_agent_messages[:] = session.cli_agent_messages[-cap:]
        return

    session.cli_agent_messages.append(("user", message))
    session.cli_agent_messages.append(("assistant", text_str))
    cap = _MAX_CLI_AGENT_TURNS * 2
    if len(session.cli_agent_messages) > cap:
        session.cli_agent_messages[:] = session.cli_agent_messages[-cap:]

    console.print()
    console.print(f"[{TERMINAL_ACCENT_BOLD}]assistant:[/]")
    # Render the answer as Markdown so tables, bold, lists, and code spans
    # display correctly in the terminal instead of leaking raw `**bold**`,
    # `| col |` table syntax, etc. (#604).
    console.print(Markdown(text_str))
    console.print()


__all__ = ["answer_cli_agent"]
