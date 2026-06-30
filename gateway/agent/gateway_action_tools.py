"""Gateway-local action tools for the headless Telegram harness surface."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import config.constants.platform as _platform
from core.agent_harness.session import ReplSession
from core.context.state import InvestigationState
from core.types import AgentToolContext
from gateway.agent.gateway_output_sink import GatewayOutputSink
from tools.interactive_shell.contracts import object_schema, string_property
from tools.interactive_shell.shell.display import format_shell_command_for_display
from tools.interactive_shell.shell.execution import ShellExecutionResult, execute_shell_command
from tools.interactive_shell.shell.parsing import parse_shell_command
from tools.interactive_shell.shell.policy import plan_shell_execution
from tools.registered_tool import RegisteredTool

GATEWAY_RESOURCE_KEY = "gateway"
_SHELL_COMMAND_TIMEOUT_SECONDS = 120
_MAX_COMMAND_OUTPUT_CHARS = 12_000
_MAX_GATEWAY_RESPONSE_CHARS = 3_500


@dataclass(frozen=True)
class GatewayToolContext:
    """Runtime context for gateway action tools (no Rich console)."""

    session: ReplSession
    sink: GatewayOutputSink
    chat_id: str
    confirm_fn: Callable[[str], str] | None = None
    is_tty: bool | None = None
    action_already_listed: bool = False


def normalize_investigation_alert_text(raw: str) -> str:
    """Strip outer quotes models often echo from user-quoted investigation payloads."""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1].strip()
    return value


def gateway_context_from_agent_context(context: AgentToolContext) -> GatewayToolContext:
    gateway_context = context.resources.get(GATEWAY_RESOURCE_KEY)
    if not isinstance(gateway_context, GatewayToolContext):
        raise RuntimeError("gateway action tool requires gateway runtime context")
    return gateway_context


def execute_with_gateway_context(
    args: dict[str, Any],
    context: AgentToolContext,
    execute: Callable[[dict[str, Any], GatewayToolContext], bool],
) -> dict[str, Any]:
    gateway_context = gateway_context_from_agent_context(context)
    return {"ok": bool(execute(args, gateway_context))}


def _truncate_response(text: str) -> str:
    if len(text) <= _MAX_GATEWAY_RESPONSE_CHARS:
        return text
    return text[: _MAX_GATEWAY_RESPONSE_CHARS - 1].rstrip() + "…"


def _format_shell_result(result: ShellExecutionResult) -> str:
    parts: list[str] = []
    if (result.stdout or "").strip():
        parts.append(result.stdout.strip())
    if (result.stderr or "").strip():
        parts.append(result.stderr.strip())
    if result.timed_out:
        prefix = f"Command timed out after {_SHELL_COMMAND_TIMEOUT_SECONDS} seconds"
        return _truncate_response("\n".join([prefix, *parts]) if parts else prefix)
    if result.exit_code not in (0, None):
        code = result.exit_code if result.exit_code is not None else "?"
        parts.append(f"exit {code}")
    if not parts:
        return "Command completed successfully."
    return _truncate_response("\n".join(parts))


def _resolved_integrations(session: ReplSession) -> dict[str, Any]:
    cached = session.resolved_integrations_cache
    if not isinstance(cached, dict):
        return {}
    return {key: value for key, value in cached.items() if not str(key).startswith("_")}


def _summarize_investigation_state(state: InvestigationState) -> str:
    from tools.investigation.reporting.context.build import build_report_context
    from tools.investigation.reporting.formatters.report import format_telegram_message

    report = format_telegram_message(build_report_context(state)).strip()
    if report:
        return _truncate_response(report)
    summary = str(state.get("summary") or state.get("report") or "").strip()
    if summary:
        return _truncate_response(summary)
    root_cause = str(state.get("root_cause") or "").strip()
    if root_cause:
        return _truncate_response(root_cause)
    return "Investigation completed."


def execute_shell_tool(args: dict[str, Any], ctx: GatewayToolContext) -> bool:
    command = str(args.get("command", "")).strip()
    if not command:
        return False

    parsed = parse_shell_command(command, is_windows=_platform.IS_WINDOWS)
    plan = plan_shell_execution(parsed)
    if plan.policy.verdict == "deny":
        reason = plan.policy.reason or "Shell command rejected."
        ctx.sink.set_tool_status(reason)
        ctx.session.record("shell", command, ok=False, response_text=reason)
        return False

    display_command = format_shell_command_for_display(command)
    ctx.sink.set_tool_status(f"Running: {display_command}")

    try:
        result = execute_shell_command(
            command=parsed.command,
            argv=parsed.argv,
            use_shell=parsed.use_shell,
            timeout_seconds=_SHELL_COMMAND_TIMEOUT_SECONDS,
            max_output_chars=_MAX_COMMAND_OUTPUT_CHARS,
        )
    except Exception as exc:
        response_text = f"command failed to start: {exc}"
        ctx.sink.set_tool_status(response_text)
        ctx.session.record("shell", command, ok=False, response_text=response_text)
        return False

    response_text = _format_shell_result(result)
    ok = not result.timed_out and result.exit_code == 0
    ctx.session.record("shell", command, ok=ok, response_text=response_text)
    return ok


def execute_investigation_tool(args: dict[str, Any], ctx: GatewayToolContext) -> bool:
    alert_text = normalize_investigation_alert_text(str(args.get("alert_text", "")))
    if not alert_text:
        return False

    ctx.sink.set_tool_status(f"Investigating: {alert_text[:120]}")
    try:
        from tools.investigation.capability import run_investigation

        state = run_investigation(
            alert_text,
            resolved_integrations=_resolved_integrations(ctx.session) or None,
        )
        response_text = _summarize_investigation_state(state)
        ctx.session.last_state = dict(state)
        ctx.session.record("alert", alert_text, ok=True, response_text=response_text)
        return True
    except Exception as exc:
        response_text = f"Investigation failed: {exc}"
        ctx.sink.set_tool_status(response_text)
        ctx.session.record("alert", alert_text, ok=False, response_text=response_text)
        return False


def run_shell(*, command: str, context: Any) -> dict[str, Any]:
    return execute_with_gateway_context({"command": command}, context, execute_shell_tool)


def run_investigation(*, alert_text: str, context: Any) -> dict[str, Any]:
    return execute_with_gateway_context(
        {"alert_text": alert_text},
        context,
        execute_investigation_tool,
    )


shell_run_tool = RegisteredTool(
    name="shell_run",
    description=(
        "Run a narrowly scoped local diagnostic shell command. Use for read-only inspection "
        "or controlled operational steps already requested by the user; avoid destructive, "
        "credential-exfiltrating, or unrelated commands."
    ),
    input_schema=object_schema(
        properties={
            "command": string_property(
                description=(
                    "Exact shell command to execute. Prefer safe diagnostics (for example: "
                    "`ls`, `pwd`, `git status`, `uv run python -m pytest ...`). Do not use "
                    "commands that wipe data or alter unrelated system state."
                ),
                min_length=1,
            )
        },
        required=("command",),
    ),
    source="interactive_shell",
    surfaces=("action",),
    parallel_safe=False,
    accepts_runtime_context=True,
    side_effect_level="mutating",
    run=run_shell,
)

investigation_start_tool = RegisteredTool(
    name="investigation_start",
    description=(
        "Start an investigation with the provided alert text or quoted payload. "
        "Use whenever the user explicitly instructs you to investigate, RCA, "
        "diagnose, analyze, root-cause, or send an investigation payload."
    ),
    input_schema=object_schema(
        properties={
            "alert_text": string_property(
                description="Alert text or incident details to investigate.",
                min_length=1,
            )
        },
        required=("alert_text",),
    ),
    source="interactive_shell",
    surfaces=("action",),
    parallel_safe=False,
    accepts_runtime_context=True,
    side_effect_level="external",
    run=run_investigation,
)


def gateway_action_tools() -> list[RegisteredTool]:
    """Return the gateway-local action tool set for one Telegram turn."""
    return [shell_run_tool, investigation_start_tool]


__all__ = [
    "GATEWAY_RESOURCE_KEY",
    "GatewayToolContext",
    "execute_with_gateway_context",
    "gateway_action_tools",
    "gateway_context_from_agent_context",
    "investigation_start_tool",
    "normalize_investigation_alert_text",
    "shell_run_tool",
]
