"""Terminal assistant for interactive OpenSRE CLI guidance and chat."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape

from app.cli.interactive_shell.prompt_logging import LlmRunInfo
from app.cli.interactive_shell.prompting.follow_up import _summarize_last_state
from app.cli.interactive_shell.prompting.prompt_rules import (
    CLI_ASSISTANT_MARKDOWN_RULE,
    INTERACTIVE_SHELL_TERMINOLOGY_RULE,
)
from app.cli.interactive_shell.references.agents_md_reference import (
    build_agents_md_reference_text,
)
from app.cli.interactive_shell.references.cli_reference import build_cli_reference_text
from app.cli.interactive_shell.references.grounding_diagnostics import (
    log_grounding_cache_diagnostics,
)
from app.cli.interactive_shell.references.investigation_flow_reference import (
    build_investigation_flow_reference_text,
)
from app.cli.interactive_shell.runtime import ReplSession
from app.cli.interactive_shell.runtime.session import (
    SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST,
)
from app.cli.interactive_shell.ui import (
    BOLD_BRAND,
    DIM,
    ERROR,
    MARKDOWN_THEME,
    STREAM_LABEL_ASSISTANT,
    WARNING,
    stream_to_console,
)
from app.cli.support.exception_reporting import report_exception
from app.integrations.llm_cli.errors import CLITimeoutError

# Cap stored (user, assistant) pairs; list holds 2 entries per turn.
_MAX_CLI_AGENT_TURNS = 12

_MAX_SYNTHETIC_OBSERVATION_PROMPT_CHARS = 120_000


def _user_message_requests_synthetic_failure_explanation(message: str) -> bool:
    """True when the user is likely asking about a failed synthetic benchmark."""
    m = message.strip().lower()
    if not m:
        return False
    suggested = SUGGESTED_PROMPT_AFTER_FAILED_SYNTHETIC_TEST.lower().rstrip("?")
    if m.rstrip("?") == suggested:
        return True
    if "why" in m and "fail" in m:
        return True
    return "what went wrong" in m


def _load_synthetic_observation_text(
    path_str: str, *, max_chars: int = _MAX_SYNTHETIC_OBSERVATION_PROMPT_CHARS
) -> str:
    try:
        raw = Path(path_str).read_text(encoding="utf-8")
    except OSError:
        return ""
    if len(raw) > max_chars:
        return (
            raw[:max_chars]
            + f"\n… [truncated for prompt size; observation is {len(raw)} characters total]"
        )
    return raw


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
    "/model show, /list models, /health, /doctor, /version; "
    '`{"action":"run_cli_command","args":"<subcommand> <flags>"}` '
    "to run any opensre subcommand (agent is blocked). For ordinary "
    "questions, return normal Markdown. Do not return action JSON for vague "
    "local model requests such as `connect to local llama`; answer with a brief "
    "clarification or mention `/model set ollama` as an option instead."
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


def _opensre_integration_command_blocked(payload: str, session: ReplSession) -> bool:
    """Block integration-management CLI runs when the session has none configured."""
    if not session.configured_integrations_known or session.configured_integrations:
        return False
    lowered = payload.strip().lower()
    return lowered.startswith("integrations") or "integration" in lowered


def _format_history_for_prompt(session: ReplSession) -> str:
    """Render recent CLI agent turns for multi-turn context."""
    lines: list[str] = []
    cap = _MAX_CLI_AGENT_TURNS * 2
    for role, content in session.cli_agent_messages[-cap:]:
        label = "User" if role == "user" else "Assistant"
        lines.append(f"{label}: {content}")
    return "\n".join(lines) if lines else "(no prior messages in this CLI thread)"


def _build_system_prompt(
    reference: str,
    history: str,
    agents_md: str = "",
    investigation_flow: str = "",
    prior_investigation: str = "",
) -> str:
    """Build the system prompt for one assistant turn.

    Split out so tests can assert on terminology / formatting rules without
    invoking an LLM. ``agents_md`` is the optional repo-map block from
    :mod:`app.cli.interactive_shell.references.agents_md_reference`; when empty the
    section is omitted so callers in environments that ship no AGENTS.md
    files don't waste tokens on an empty header. ``investigation_flow`` is a
    concise reference to how ``opensre investigate`` processes alerts.
    """
    repo_map_block = f"--- Repo map (AGENTS.md) ---\n{agents_md}\n\n" if agents_md else ""
    investigation_flow_block = (
        f"--- Investigation flow reference ---\n{investigation_flow}\n\n"
        if investigation_flow
        else ""
    )
    prior_investigation_block = (
        f"--- Prior investigation in this session ---\n{prior_investigation}\n\n"
        if prior_investigation
        else ""
    )
    return (
        "You are the OpenSRE terminal assistant. You help with OpenSRE CLI "
        "usage, the interactive shell, and onboarding. A deterministic pre-pass "
        "runs first: it executes eligible local commands as argv (no shell) "
        "under a read-only allowlist; users must prefix with ! for full-shell "
        "semantics (pipes, redirects, mutating commands). Do not tell users the "
        "interactive shell cannot execute commands. You do NOT run incident "
        "investigations yourself "
        "(those use the separate investigation pipeline), but you are grounded on "
        "that pipeline's architecture below and can answer questions about its "
        "stages and source files.\n"
        "When the user wants to investigate an alert, tell them to paste "
        "alert text, JSON, or a concrete incident description (errors, "
        "services, symptoms). Mention `opensre investigate` and pasting "
        "into this interactive shell.\n"
        "Be brief and friendly. Ground CLI facts in the reference below; do "
        "not invent subcommands. For investigation-flow questions, use the "
        "investigation flow reference below and do not claim the pipeline "
        "definition is unavailable.\n"
        "For vague operational questions (for example why a database is slow) "
        "with no pasted alert, restate the user's question in your reply and "
        "ask for the target system, service, or alert context.\n\n"
        f"{_TERMINOLOGY_RULE}\n{_MARKDOWN_RULE}\n{_ACTION_RULE}\n\n"
        f"--- CLI reference ---\n{reference}\n\n"
        f"{investigation_flow_block}"
        f"{prior_investigation_block}"
        f"{repo_map_block}"
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
    from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_policy import (
        evaluate_llm_runtime_switch,
        evaluate_slash_tier,
        execution_allowed,
        resolve_slash_execution_tier,
    )

    console.print()
    console.print(f"[{BOLD_BRAND}]{STREAM_LABEL_ASSISTANT}:[/]")
    console.print(f"[{DIM}]Requested actions:[/]")
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
        elif kind == "run_cli_command":
            args = str(action.get("args", "")).strip()
            label = f"opensre {args}" if args else "opensre"
        else:
            label = f"unsupported action: {kind or '?'}"
        console.print(f"[{DIM}]{index}.[/] [{BOLD_BRAND}]{escape(label)}[/]")

    console.print()
    for action in actions:
        kind = str(action.get("action", "")).strip()
        console.print()
        if kind == "switch_llm_provider":
            provider = str(action.get("provider", "")).strip()
            requested_model = str(action.get("model", "")).strip() or None
            requested_toolcall = str(action.get("toolcall_model", "")).strip() or None
            if not provider:
                console.print(f"[{ERROR}]missing provider for switch_llm_provider action[/]")
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
                console.print(f"[{ERROR}]missing model for switch_toolcall_model action[/]")
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
                console.print(f"[{ERROR}]unsupported action command:[/] {escape(command)}")
                continue
            stripped = command.strip()
            parts = stripped.split()
            name = parts[0].lower()
            arg_list = parts[1:]
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
            tier = resolve_slash_execution_tier(name, arg_list, cmd_slash.execution_tier)
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

        if kind == "run_cli_command":
            args = str(action.get("args", "")).strip()
            if not args:
                console.print(f"[{ERROR}]missing args for run_cli_command action[/]")
                continue
            if _opensre_integration_command_blocked(args, session):
                console.print(
                    f"[{WARNING}]integration command blocked: no integrations are configured "
                    "in this session.[/]"
                )
                continue
            from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.action_executor import (
                run_opensre_cli_command,
            )

            run_opensre_cli_command(
                args,
                session,
                console,
                confirm_fn=confirm_fn,
                is_tty=is_tty,
            )
            continue

        console.print(f"[{ERROR}]unsupported action:[/] {escape(kind or '?')}")
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
    *,
    confirm_fn: Callable[[str], str] | None = None,
) -> LlmRunInfo | None:
    """Run one turn of the terminal assistant (guidance only; no investigation run).

    For documentation-grounded procedural Q&A use :func:`answer_cli_help`, which
    also pulls relevant ``docs/`` pages into the grounding context.

    ``confirm_fn`` is forwarded to :func:`_execute_action_plan` so the
    interactive REPL can route mid-dispatch ``Proceed? [y/N]`` prompts
    through its active prompt_toolkit input instead of the stdlib
    ``input()`` (which deadlocks against the running ``prompt_async``).
    """
    try:
        from app.services.llm_client import get_llm_for_reasoning
    except Exception as exc:
        report_exception(exc, context="interactive_shell.cli_agent.import")
        console.print(f"[{ERROR}]LLM client unavailable:[/] {escape(str(exc))}")
        return None

    reference = build_cli_reference_text()
    agents_md = build_agents_md_reference_text()
    investigation_flow = build_investigation_flow_reference_text()
    log_grounding_cache_diagnostics("cli_agent_grounding")
    history = _format_history_for_prompt(session)
    prior_investigation = (
        _summarize_last_state(session.last_state) if session.last_state is not None else ""
    )
    integration_guard = ""
    if session.configured_integrations_known and not session.configured_integrations:
        integration_guard = (
            "No integrations are configured in this session. Do not emit run_cli_command "
            "or slash actions for integration setup/show/verify/remove; answer with guidance "
            "only.\n\n"
        )
    system = _build_system_prompt(
        reference,
        history,
        agents_md=agents_md,
        investigation_flow=investigation_flow,
        prior_investigation=prior_investigation,
    )
    user_block = f"--- User message ---\n{message}"
    synthetic_block = ""
    obs_path = session.last_synthetic_observation_path
    if obs_path and _user_message_requests_synthetic_failure_explanation(message):
        obs_text = _load_synthetic_observation_text(obs_path)
        if obs_text:
            synthetic_block = (
                "The user is asking about a failed `opensre tests synthetic` run "
                "in this checkout. The JSON below is the saved observation "
                f"(scores, gates, stderr summary). Path: {obs_path}\n"
                "Use it to explain validation failures. Do not say nothing ran or "
                "that you lack context — the run completed and this file was written.\n\n"
                f"--- observation_json ---\n{obs_text}\n\n"
            )
    prompt = f"{system}\n{integration_guard}{synthetic_block}{user_block}"

    try:
        client = get_llm_for_reasoning()
        started = time.monotonic()
        text_str = stream_to_console(
            console,
            label=STREAM_LABEL_ASSISTANT,
            chunks=client.invoke_stream(prompt),
            # Suppress the live render if the model is emitting a JSON action
            # plan: that payload is consumed by ``_execute_action_plan`` and
            # would otherwise leak raw braces to the user (#1263).
            suppress_if_starts_with="{",
        )
    except KeyboardInterrupt:
        console.print(f"[{DIM}]· cancelled[/]")
        return None
    except Exception as exc:
        report_exception(
            exc,
            context="interactive_shell.cli_agent.stream",
            expected=isinstance(exc, CLITimeoutError),
        )
        console.print(f"[{ERROR}]assistant failed:[/] {escape(str(exc))}")
        return None

    run_info = LlmRunInfo(
        model=_resolve_model_name(client),
        provider=_resolve_provider_name(client),
        latency_ms=int((time.monotonic() - started) * 1000),
        response_text=text_str,
    )

    actions = _parse_action_plan(text_str)
    if _execute_action_plan(actions, session, console, confirm_fn=confirm_fn):
        _record_cli_agent_turn(session, message, text_str)
        return run_info

    _record_cli_agent_turn(session, message, text_str)

    # If the response was suppressed (looked like a JSON action plan) but no
    # valid actions parsed, render it now as Markdown so the user sees
    # something. The non-suppressed path was already rendered live.
    if text_str.lstrip().startswith("{") and text_str.strip():
        console.print()
        console.print(f"[{BOLD_BRAND}]{STREAM_LABEL_ASSISTANT}:[/]")
        with console.use_theme(MARKDOWN_THEME):
            console.print(Markdown(text_str, code_theme="ansi_dark"))
        console.print()
    return run_info


def _resolve_model_name(client: object) -> str | None:
    value = getattr(client, "_model", None)
    return value if isinstance(value, str) and value else None


def _resolve_provider_name(client: object) -> str | None:
    provider_label = getattr(client, "_provider_label", None)
    if isinstance(provider_label, str) and provider_label:
        return provider_label.strip().lower().replace(" ", "_")
    name = type(client).__name__.lower()
    if "openai" in name:
        return "openai"
    if "bedrock" in name:
        return "bedrock"
    if "cli" in name:
        return "cli"
    if "anthropic" in name or "llmclient" in name:
        return "anthropic"
    return None


__all__ = ["answer_cli_agent"]
