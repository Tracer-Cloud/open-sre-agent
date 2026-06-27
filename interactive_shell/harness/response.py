"""Final response generation for one interactive-shell turn.

Generates the user-facing assistant reply: builds the prompt, streams the model
response, executes any embedded action plan (parsed via
:mod:`interactive_shell.harness.action_plan` and executed via
:mod:`interactive_shell.harness.action_exec`), and records the turn into the
session's conversational-agent state. Guidance only; no investigation run.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.markup import escape

from integrations.llm_cli.errors import CLITimeoutError
from interactive_shell.harness.action_exec import _execute_action_plan
from interactive_shell.harness.action_plan import _parse_action_plan
from interactive_shell.harness.agent_context import AgentContext
from interactive_shell.harness.llm_context.assistant_prompt import build_cli_agent_prompt
from interactive_shell.runtime import ReplSession
from interactive_shell.runtime.agent_presentation import render_json_like_response
from interactive_shell.runtime.core.token_accounting import build_llm_run_info
from interactive_shell.ui import (
    DIM,
    ERROR,
    STREAM_LABEL_ASSISTANT,
    stream_to_console,
)
from interactive_shell.utils.error_handling.exception_reporting import report_exception
from interactive_shell.utils.telemetry import LlmRunInfo


def _load_reasoning_client(console: Console) -> Any | None:
    try:
        from core.runtime.llm.llm_client import get_llm_for_reasoning
    except Exception as exc:
        report_exception(exc, context="interactive_shell.cli_agent.import")
        console.print(f"[{ERROR}]LLM client unavailable:[/] {escape(str(exc))}")
        return None

    return get_llm_for_reasoning()


def _stream_response(
    *,
    client: Any,
    prompt: str,
    session: ReplSession,
    console: Console,
) -> LlmRunInfo | None:
    try:
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

    return build_llm_run_info(
        session=session,
        prompt=prompt,
        response_text=text_str,
        started=started,
        client=client,
    )


def generate_response(
    message: str,
    session: ReplSession,
    console: Console,
    *,
    confirm_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    tool_observation: str | None = None,
    tool_observation_on_screen: bool = True,
    agent_ctx: AgentContext | None = None,
) -> LlmRunInfo | None:
    """Generate one turn's final assistant response (guidance only; no investigation run).

    ``agent_ctx`` is the immutable per-prompt snapshot assembled at prompt start.
    When present, snapshot fields (conversation history, integration state,
    prior investigation, synthetic-run path) are read from it rather than from
    the live session, so prompt construction reflects a stable prompt-start view.
    """
    client = _load_reasoning_client(console)
    if client is None:
        return None

    ctx = agent_ctx or AgentContext.from_session(message, session)

    prompt = build_cli_agent_prompt(
        message=message,
        session=session,
        tool_observation=tool_observation,
        tool_observation_on_screen=tool_observation_on_screen,
        agent_ctx=ctx,
    )

    run_info = _stream_response(
        client=client,
        prompt=prompt,
        session=session,
        console=console,
    )
    if run_info is None:
        return None

    text_str = run_info.response_text or ""
    handled = _execute_action_plan(
        _parse_action_plan(text_str),
        session,
        console,
        confirm_fn=confirm_fn,
        is_tty=is_tty,
    )

    session.agent.record_turn(message, text_str)

    if not handled:
        render_json_like_response(console, text_str)

    return run_info


__all__ = [
    "generate_response",
]
