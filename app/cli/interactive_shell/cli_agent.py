"""LangGraph-free assistant for the interactive terminal - CLI guidance and chat."""

from __future__ import annotations

import json
from collections.abc import Callable

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape

from app.cli.interactive_shell.cli_reference import build_cli_reference_text
from app.cli.interactive_shell.grounding_diagnostics import log_grounding_cache_diagnostics
from app.cli.interactive_shell.loaders import llm_loader
from app.cli.interactive_shell.prompt_rules import (
    CLI_ASSISTANT_MARKDOWN_RULE,
    INTERACTIVE_SHELL_TERMINOLOGY_RULE,
)
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.theme import TERMINAL_ACCENT_BOLD

# Cap stored (user, assistant) pairs; list holds 2 entries per turn.
_MAX_CLI_AGENT_TURNS = 12

_TERMINOLOGY_RULE = INTERACTIVE_SHELL_TERMINOLOGY_RULE
_MARKDOWN_RULE = CLI_ASSISTANT_MARKDOWN_RULE

_ACTION_RULE = (
    "Action planning: if the user asks you to change OpenSRE runtime state, "
    "return ONLY a compact JSON object with an `actions` array. Do not give "
    "instructions when an allowed action can satisfy the request. Allowed "
    "action object schemas: "
    '`{"action":"switch_llm_provider","provider":"anthropic","model":"","toolcall_model":""}` '
    "where provider is one of anthropic, openai, openrouter, gemini, nvidia, "
    "ollama, codex, claude-code, gemini-cli; both `model` (reasoning) and `toolcall_model` are optional; "
    '`{"action":"switch_toolcall_model","model":"claude-opus-4-7"}` '
    "to change ONLY the toolcall model on the currently active provider; "
    '`{"action":"slash","command":"/model show"}` where command is one of '
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


def _build_system_prompt(reference: str, history: str) -> str:
    """Build the system prompt for one assistant turn.

    Split out so tests can assert on terminology / formatting rules without
    invoking an LLM.
    """
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


def _response_text(response: object) -> str:
    """Extract text from heterogeneous LLM response content payloads."""
    content = getattr(response, "content", None)
    if content is None:
        return str(response)
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        text_value = content.get("text")
        return text_value if isinstance(text_value, str) else str(content)
    if isinstance(content, list):
        blocks: list[str] = []
        for item in content:
            if isinstance(item, str):
                blocks.append(item)
                continue
            if isinstance(item, dict):
                text_value = item.get("text")
                blocks.append(text_value if isinstance(text_value, str) else str(item))
                continue
            text_value = getattr(item, "text", None)
            blocks.append(text_value if isinstance(text_value, str) else str(item))
        joined = "\n".join(part for part in blocks if part.strip()).strip()
        return joined or str(content)
    text_value = getattr(content, "text", None)
    if isinstance(text_value, str):
        return text_value
    return str(content)


def _execute_action_plan(
    actions: list[dict[str, object]],
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
) -> bool:
    if not actions:
        return False

    from app.cli.interactive_shell.commands import (
        SLASH_COMMANDS,
        dispatch_slash,
        switch_llm_provider,
        switch_toolcall_model,
    )
    from app.cli.interactive_shell.execution_policy import (
        evaluate_llm_runtime_switch,
        evaluate_slash_tier,
        execution_allowed,
        resolve_slash_execution_tier,
    )

    console.print()
    console.print(f"[{TERMINAL_ACCENT_BOLD}]assistant:[/]")
    console.print("[dim]Requested actions:[/dim]")
    for index, action in enumerate(actions, start=1):
        kind = str(action.get("action", "")).strip()
        if kind == "switch_llm_provider":
            provider = str(action.get("provider", "")).strip()
            model = str(action.get("model", "")).strip()
            toolcall = str(action.get("toolcall_model", "")).strip()
            label = f"switch LLM provider to {provider}"
            if model:
                label += f" ({model})"
            if toolcall:
                label += f" + toolcall {toolcall}"
        elif kind == "switch_toolcall_model":
            requested = str(action.get("model", "")).strip()
            label = (
                f"switch toolcall model to {requested}" if requested else "switch toolcall model"
            )
        elif kind == "slash":
            label = str(action.get("command", "")).strip()
        else:
            label = f"unsupported action: {kind or '?'}"
        console.print(f"[dim]{index}.[/dim] [{TERMINAL_ACCENT_BOLD}]{escape(label)}[/]")

    console.print()
    for action in actions:
        kind = str(action.get("action", "")).strip()
        console.print()
        if kind == "switch_llm_provider":
            provider = str(action.get("provider", "")).strip()
            requested_model = str(action.get("model", "")).strip() or None
            requested_toolcall = str(action.get("toolcall_model", "")).strip() or None
            if not provider:
                console.print("[red]missing provider for switch_llm_provider action[/red]")
                continue
            slash_label = f"/model set {provider}"
            if requested_model:
                slash_label += f" {requested_model}"
            if requested_toolcall:
                slash_label += f" --toolcall-model {requested_toolcall}"
            pol = evaluate_llm_runtime_switch(action_type="switch_llm_provider")
            if not execution_allowed(
                pol,
                session=session,
                console=console,
                action_summary=slash_label,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
                action_already_listed=True,
            ):
                continue
            console.print(f"[bold]$ {escape(slash_label)}[/bold]")
            switch_llm_provider(
                provider,
                console,
                model=requested_model,
                toolcall_model=requested_toolcall,
            )
            session.record("slash", slash_label)
            continue

        if kind == "switch_toolcall_model":
            requested_model = str(action.get("model", "")).strip()
            if not requested_model:
                console.print("[red]missing model for switch_toolcall_model action[/red]")
                continue
            pol = evaluate_llm_runtime_switch(action_type="switch_toolcall_model")
            if not execution_allowed(
                pol,
                session=session,
                console=console,
                action_summary=f"/model toolcall set {requested_model}",
                confirm_fn=confirm_fn,
                is_tty=is_tty,
                action_already_listed=True,
            ):
                continue
            console.print(f"[bold]$ /model toolcall set {escape(requested_model)}[/bold]")
            switch_toolcall_model(requested_model, console)
            session.record("slash", f"/model toolcall set {requested_model}")
            continue

        if kind == "slash":
            command = str(action.get("command", "")).strip()
            if command not in _ALLOWED_SLASH_ACTIONS:
                console.print(f"[red]unsupported action command:[/red] {escape(command)}")
                continue
            stripped = command.strip()
            parts = stripped.split()
            name = parts[0].lower()
            args = parts[1:]
            cmd_slash = SLASH_COMMANDS.get(name)
            if cmd_slash is None:
                dispatch_slash(
                    command,
                    session,
                    console,
                    confirm_fn=confirm_fn,
                    is_tty=is_tty,
                )
                continue
            tier = resolve_slash_execution_tier(name, args, cmd_slash.execution_tier)
            policy = evaluate_slash_tier(tier)
            if not execution_allowed(
                policy,
                session=session,
                console=console,
                action_summary=stripped,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
                action_already_listed=True,
            ):
                session.record("slash", stripped, ok=False)
                continue
            console.print(f"[bold]$ {escape(command)}[/bold]")
            dispatch_slash(
                command,
                session,
                console,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
                policy_precleared=True,
            )
            continue

        console.print(f"[red]unsupported action:[/red] {escape(kind or '?')}")
    console.print()
    return True


def _record_cli_agent_turn(session: ReplSession, message: str, assistant_text: str) -> None:
    session.cli_agent_messages.append(("user", message))
    session.cli_agent_messages.append(("assistant", assistant_text))
    cap = _MAX_CLI_AGENT_TURNS * 2
    if len(session.cli_agent_messages) > cap:
        session.cli_agent_messages[:] = session.cli_agent_messages[-cap:]


def answer_cli_agent(
    message: str,
    session: ReplSession,
    console: Console,
) -> None:
    """Run one turn of the terminal assistant (no LangGraph / no investigation pipeline).

    For documentation-grounded procedural Q&A use :func:`answer_cli_help`, which
    also pulls relevant ``docs/`` pages into the grounding context.
    """
    try:
        from app.services.llm_client import get_llm_for_reasoning
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]LLM client unavailable:[/red] {escape(str(exc))}")
        return

    reference = build_cli_reference_text()
    log_grounding_cache_diagnostics("cli_agent_grounding")
    history = _format_history_for_prompt(session)
    system = _build_system_prompt(reference, history)
    user_block = f"--- User message ---\n{message}"
    prompt = f"{system}\n{user_block}"

    try:
        with llm_loader(console):
            client = get_llm_for_reasoning()
            response = client.invoke(prompt)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]assistant failed:[/red] {escape(str(exc))}")
        return

    text_str = _response_text(response)
    actions = _parse_action_plan(text_str)
    if _execute_action_plan(actions, session, console):
        _record_cli_agent_turn(session, message, text_str)
        return

    _record_cli_agent_turn(session, message, text_str)

    console.print()
    console.print(f"[{TERMINAL_ACCENT_BOLD}]assistant:[/]")
    # Render the answer as Markdown so tables, bold, lists, and code spans
    # display correctly in the terminal instead of leaking raw `**bold**`,
    # `| col |` table syntax, etc. (#604).
    console.print(Markdown(text_str))
    console.print()


__all__ = ["answer_cli_agent"]
