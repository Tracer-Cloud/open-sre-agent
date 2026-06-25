"""Terminal assistant for interactive OpenSRE CLI guidance and chat."""

from __future__ import annotations

import time
from collections.abc import Callable

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape

from cli.interactive_shell.chat.action_plan import (
    _ALLOWED_SLASH_ACTIONS,
    _actions_allowed_by_capabilities,
    _opensre_integration_command_blocked,
    _parse_action_plan,
    _registered_interactive_command,
)
from cli.interactive_shell.chat.message_classifier import (
    _load_synthetic_observation_text,
    _user_message_requests_synthetic_failure_explanation,
)
from cli.interactive_shell.chat.system_prompt import (
    _build_environment_block,
    _build_observation_block,
    _build_system_prompt,
)
from cli.interactive_shell.error_handling.exception_reporting import report_exception
from cli.interactive_shell.prompt_logging import LlmRunInfo
from cli.interactive_shell.prompting.conversation_history import (
    MAX_CONVERSATION_MESSAGES,
    format_recent_conversation,
)
from cli.interactive_shell.prompting.follow_up import _summarize_last_state
from cli.interactive_shell.references.agents_md_reference import (
    build_agents_md_reference_text,
)
from cli.interactive_shell.references.cli_reference import build_cli_reference_text
from cli.interactive_shell.references.grounding_diagnostics import (
    log_grounding_cache_diagnostics,
)
from cli.interactive_shell.references.investigation_flow_reference import (
    build_investigation_flow_reference_text,
)
from cli.interactive_shell.runtime import ReplSession
from cli.interactive_shell.token_accounting import build_llm_run_info
from cli.interactive_shell.ui import (
    BOLD_BRAND,
    DIM,
    ERROR,
    MARKDOWN_THEME,
    STREAM_LABEL_ASSISTANT,
    WARNING,
    stream_to_console,
)
from integrations.llm_cli.errors import CLITimeoutError


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

    actions = _actions_allowed_by_capabilities(actions, session)
    if not actions:
        return False

    from cli.interactive_shell.command_registry import (
        SLASH_COMMANDS,
        dispatch_slash,
        switch_llm_provider,
        switch_toolcall_model,
    )
    from cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_policy import (
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
        elif kind == "run_interactive":
            label = str(action.get("command", "")).strip() or "interactive command"
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
            from cli.interactive_shell.routing.handle_message_with_agent.orchestration.action_executor import (
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

        if kind == "run_interactive":
            command = str(action.get("command", "")).strip()
            if not _registered_interactive_command(command):
                console.print(f"[{ERROR}]unsupported interactive command:[/] {escape(command)}")
                continue
            from cli.interactive_shell.ui.choice_menu import repl_tty_interactive

            if not repl_tty_interactive():
                console.print(
                    f"Run [bold]{escape(command)}[/bold] in the interactive shell to continue."
                )
                continue
            console.print(f"[{DIM}]Launching[/] [{BOLD_BRAND}]{escape(command)}[/]…")
            session.queue_auto_command(command)
            continue

        console.print(f"[{ERROR}]unsupported action:[/] {escape(kind or '?')}")
    console.print()
    return True


def _record_cli_agent_turn(session: ReplSession, message: str, assistant_text: str) -> None:
    session.cli_agent_messages.append(("user", message))
    session.cli_agent_messages.append(("assistant", assistant_text))
    if len(session.cli_agent_messages) > MAX_CONVERSATION_MESSAGES:
        session.cli_agent_messages[:] = session.cli_agent_messages[-MAX_CONVERSATION_MESSAGES:]


def answer_cli_agent(
    message: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    tool_observation: str | None = None,
    tool_observation_on_screen: bool = True,
) -> LlmRunInfo | None:
    """Run one turn of the terminal assistant (guidance only; no investigation run)."""
    try:
        from services.llm_client import get_llm_for_reasoning
    except Exception as exc:
        report_exception(exc, context="interactive_shell.cli_agent.import")
        console.print(f"[{ERROR}]LLM client unavailable:[/] {escape(str(exc))}")
        return None

    reference = build_cli_reference_text()
    agents_md = build_agents_md_reference_text()
    investigation_flow = build_investigation_flow_reference_text()
    log_grounding_cache_diagnostics("cli_agent_grounding")
    history = format_recent_conversation(session)
    prior_investigation = (
        _summarize_last_state(session.last_state) if session.last_state is not None else ""
    )
    integration_guard = ""
    if session.configured_integrations_known and not session.configured_integrations:
        integration_guard = (
            "No integrations are configured in this session. You may still help the user "
            "configure one: when they ask to set up, connect, or add an integration, emit a "
            "run_interactive action for `/integrations setup <service>` (or `/mcp connect "
            "<server>`). Do NOT emit run_cli_command or slash actions to show/verify/remove "
            "integrations that are not configured; for those, answer with guidance only.\n\n"
        )
    system = _build_system_prompt(
        reference,
        history,
        agents_md=agents_md,
        investigation_flow=investigation_flow,
        prior_investigation=prior_investigation,
        environment=_build_environment_block(session),
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
    observation_block = _build_observation_block(
        tool_observation, on_screen=tool_observation_on_screen
    )
    prompt = f"{system}\n{integration_guard}{observation_block}{synthetic_block}{user_block}"

    try:
        client = get_llm_for_reasoning()
        started = time.monotonic()
        text_str = stream_to_console(
            console,
            label=STREAM_LABEL_ASSISTANT,
            chunks=client.invoke_stream(prompt),
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

    run_info = build_llm_run_info(
        session=session,
        prompt=prompt,
        response_text=text_str,
        started=started,
        client=client,
    )

    actions = _parse_action_plan(text_str)
    if _execute_action_plan(
        actions,
        session,
        console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
    ):
        _record_cli_agent_turn(session, message, text_str)
        return run_info

    _record_cli_agent_turn(session, message, text_str)

    if text_str.lstrip().startswith("{") and text_str.strip():
        console.print()
        console.print(f"[{BOLD_BRAND}]{STREAM_LABEL_ASSISTANT}:[/]")
        with console.use_theme(MARKDOWN_THEME):
            console.print(Markdown(text_str, code_theme="ansi_dark"))
        console.print()
    return run_info


__all__ = ["answer_cli_agent"]
