"""MCP-style slash-command catalog for LLM planners and tool specs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cli.interactive_shell.command_registry.types import SlashCommand
from cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_tier import (
    ExecutionTier,
)
from cli.interactive_shell.routing.handle_message_with_agent.orchestration.tool_contracts import (
    object_schema,
    string_array_property,
    string_property,
)

_MAX_COMPACT_DESC_CHARS = 120


@dataclass(frozen=True)
class SlashCommandSpec:
    name: str
    description: str
    llm_description: str
    use_cases: tuple[str, ...]
    anti_examples: tuple[str, ...]
    usage: tuple[str, ...]
    examples: tuple[str, ...]
    execution_tier: ExecutionTier
    args_schema: dict[str, Any] | None


@dataclass(frozen=True)
class _SlashMcpFields:
    llm_description: str
    use_cases: tuple[str, ...]
    anti_examples: tuple[str, ...] = ()


def _mcp(
    llm_description: str,
    *use_cases: str,
    anti_examples: tuple[str, ...] = (),
) -> _SlashMcpFields:
    return _SlashMcpFields(
        llm_description=llm_description,
        use_cases=use_cases,
        anti_examples=anti_examples,
    )


_MCP_BY_COMMAND: dict[str, _SlashMcpFields] = {
    "/?": _mcp(
        "Shortcut for /help — open the interactive slash-command help browser.",
        "User types ? or asks for command help via the shortcut alias",
        anti_examples=("User asks a docs/how-to question about OpenSRE features",),
    ),
    "/alerts": _mcp(
        "Show status of the local alert listener inbox: queue depth, dropped count, "
        "and the most recent ingested alerts.",
        "User asks about the alert inbox, listener, or queued alerts",
        anti_examples=("User wants to investigate an alert body (use investigation_start)",),
    ),
    "/background": _mcp(
        "Manage session-local background investigation mode and completed RCA summaries. "
        "Subcommands: on, off, status, list, show <task_id>, use <task_id>, notify list, notify set.",
        "User asks to enable or disable background investigation mode",
        "User asks to list or inspect completed background RCAs",
        "User asks to configure background RCA notification channels",
    ),
    "/cancel": _mcp(
        "Cancel a running background task by task id. Requires confirmation in non-trust mode.",
        "User asks to cancel a specific task when they provide or imply a task id",
        anti_examples=("User asks to stop everything without an id (use /stop guidance first)",),
    ),
    "/clear": _mcp(
        "Clear the terminal screen and re-render the OpenSRE banner.",
        "User asks to clear the screen or terminal",
    ),
    "/compact": _mcp(
        "Trim old in-memory session history to reduce memory use while keeping recent context.",
        "User asks to compact, trim, or free memory from session history",
    ),
    "/config": _mcp(
        "Show or edit local OpenSRE configuration (~/.opensre/config.yml). "
        "Subcommands: show, set <key> <value>.",
        "User asks to view or change OpenSRE config settings",
        anti_examples=("User asks how to configure an integration (may need assistant_handoff)",),
    ),
    "/context": _mcp(
        "Display accumulated infrastructure context collected during the session.",
        "User asks what context or infra metadata the session has accumulated",
    ),
    "/cost": _mcp(
        "Show token usage and estimated session cost for LLM calls in this REPL session.",
        "User asks about token usage, cost, or spend in the current session",
    ),
    "/cron": _mcp(
        "Manage cron-driven scheduled deliveries. "
        "Subcommands: list, add, remove <id>, run <id>, logs <id>.",
        "User asks to list, add, remove, run, or view logs for scheduled delivery tasks",
        anti_examples=("User asks about one-off messaging without a schedule (use /messaging)",),
    ),
    "/doctor": _mcp(
        "Run a full local environment diagnostic and print pass/warn/fail per check.",
        "User asks for environment diagnostics, setup validation, or doctor check",
        anti_examples=("User only wants integration health (use /health instead)",),
    ),
    "/effort": _mcp(
        "Set REPL reasoning effort level: low, medium, high, xhigh, or max.",
        "User asks to change reasoning effort or depth for the active model",
        anti_examples=("User asks to switch provider or model name (use /model)",),
    ),
    "/exit": _mcp(
        "Exit the interactive shell and return to the parent terminal.",
        "User asks to exit, quit, or leave the REPL",
    ),
    "/fleet": _mcp(
        "Show and manage the local AI agent fleet (Claude Code, Cursor, Aider, etc.). "
        "Subcommands include budget, bus, claim, conflicts, kill, release, trace, wait, graph.",
        "User asks to list, scan, or manage local coding agents",
        anti_examples=("User asks about remote/hosted agents only",),
    ),
    "/guardrails": _mcp(
        "Manage sensitive-information guardrail rules. Subcommands: audit, init, rules, test.",
        "User asks about guardrails, PII rules, or sensitive-data masking configuration",
    ),
    "/health": _mcp(
        "Run a read-only health check of the local OpenSRE agent, LLM connectivity, "
        "and configured integrations with pass/fail per component.",
        "User asks if OpenSRE is healthy, working, or connected",
        anti_examples=(
            "User asks what integrations OpenSRE supports in general (docs → assistant_handoff)",
            "User asks to list connected integrations (use /integrations list)",
        ),
    ),
    "/help": _mcp(
        "Show the slash-command help index or detailed help for a command or category.",
        "User asks for available commands or help using /help",
        anti_examples=("User asks a procedural docs question (assistant_handoff)",),
    ),
    "/hermes": _mcp(
        "Live-tail Hermes logs and route detected incidents to Telegram. Subcommand: watch.",
        "User asks to watch Hermes logs or Hermes incident routing",
    ),
    "/history": _mcp(
        "Manage persisted command history: clear, off, on, retention <N>.",
        "User asks to clear, disable, or configure command history persistence",
    ),
    "/integrations": _mcp(
        "Manage configured integrations. Subcommands: list, verify, show <service>, remove.",
        "User asks to verify an integration by name",
        "User asks to show details for a configured integration",
        anti_examples=(
            "User asks which integrations OpenSRE supports without configuring (assistant_handoff)",
            "User asks to list connected integrations (prefer /integrations list)",
        ),
    ),
    "/investigate": _mcp(
        "Run an RCA investigation from a local alert file path or a built-in sample template.",
        "User asks to investigate a file path or run RCA from a saved alert file",
        "User asks to run one of the built-in sample alerts/templates",
        anti_examples=(
            "User pastes alert text inline (use investigation_start instead)",
            "User asks how investigations work (assistant_handoff)",
        ),
    ),
    "/last": _mcp(
        "Reprint the most recent investigation report from this session.",
        "User asks to show the last investigation result or report again",
    ),
    "/mcp": _mcp(
        "Manage connected MCP servers. Subcommands: list, connect, disconnect.",
        "User asks to list, connect, or disconnect MCP servers",
        anti_examples=("User asks about remote deployments or remote agents (use /remote)",),
    ),
    "/messaging": _mcp(
        "Manage messaging security and Telegram identities. Subcommands: pair, allow, revoke, status.",
        "User asks about Telegram pairing, messaging allowlist, or messaging status",
    ),
    "/misses": _mcp(
        "Triage investigation misses and export them as benchmark regression scenarios. "
        "Subcommands: list, stats, export --out <dir>, convert <miss_id>.",
        "User asks about investigation misses, miss triage, or miss trends",
        "User asks to convert recent misses into regression scenarios or evals",
        anti_examples=(
            "User asks for raw feedback ratings without taxonomy (read ~/.opensre/feedback.jsonl directly)",
        ),
    ),
    "/model": _mcp(
        "Show or change active LLM provider and models. Subcommands: show, set, restore, toolcall.",
        "User asks to show current model or provider settings",
        anti_examples=(
            "User says switch to local llama without a concrete provider (assistant_handoff)",
        ),
    ),
    "/onboard": _mcp(
        "Launch the interactive onboarding wizard (handoff if run inside the REPL).",
        "User asks to run onboarding or initial setup wizard",
    ),
    "/privacy": _mcp(
        "Show history persistence settings, redaction status, and the local threat model.",
        "User asks about privacy, history encryption, or data retention in the shell",
    ),
    "/quit": _mcp(
        "Alias for /exit — leave the interactive shell.",
        "User asks to quit the REPL",
    ),
    "/remote": _mcp(
        "Connect to, list, and operate remote deployed OpenSRE agents. "
        "Subcommands: health, investigate, ops, pull, trigger.",
        "User explicitly asks to connect to a remote/hosted/EC2/Nitro OpenSRE instance",
        "User asks how many remote deployments are configured or wants to inspect a remote agent",
        "User asks about remote deployment status, health, or operations",
        anti_examples=("Vague connect to X without remote/hosted context (assistant_handoff)",),
    ),
    "/new": _mcp(
        "Start a new session while preserving the current LLM conversation context and "
        "accumulated infra context. Rotates the session ID and resets all session state "
        "while keeping the conversation thread so you can continue seamlessly in a fresh session file.",
        "User wants to continue a conversation in a new session after /resume",
        "User asks to start a new session without losing their current conversation",
        anti_examples=(
            "User wants to clear the screen (use /clear)",
            "User asks to list sessions (use /sessions)",
        ),
    ),
    "/resume": _mcp(
        "Restore the conversation context from a previous session. "
        "Bare /resume opens an interactive numbered picker. "
        "Pass a session ID prefix or a name substring to resume directly "
        "(e.g. /resume 9b2e4f7a or /resume redis).",
        "User asks to resume or continue a previous session",
        "User wants to pick up where they left off in an earlier REPL session",
        "User types /resume with no argument to pick from a list",
        anti_examples=(
            "User asks to list sessions (use /sessions)",
            "User asks to start a new session keeping context (use /new)",
        ),
    ),
    "/save": _mcp(
        "Save the last investigation report to a file path. Requires confirmation.",
        "User asks to export or save the last investigation to disk",
    ),
    "/sessions": _mcp(
        "List recent REPL sessions stored on disk. Shows session ID, start time, duration, "
        "total turns, and investigation count for each session.",
        "User asks to see past sessions, session history, or what was run in previous sessions",
        anti_examples=("User asks for the current session status (use /status)",),
    ),
    "/status": _mcp(
        "Show REPL session status: provider, models, trust mode, and active flags.",
        "User asks for session status",
        anti_examples=("User asks if integrations are healthy (use /health)",),
    ),
    "/stop": _mcp(
        "Print guidance for stopping in-flight investigations and background tasks.",
        "User asks how to stop a running investigation or background work",
        anti_examples=("User provides a task id to cancel (use /cancel)",),
    ),
    "/tasks": _mcp(
        "List recent and in-flight shell background tasks with ids and status.",
        "User asks to list running or recent tasks",
    ),
    "/template": _mcp(
        "Print a starter alert JSON template (generic, datadog, grafana, honeycomb, coralogix, splunk).",
        "User asks for an alert template or example payload format",
    ),
    "/tools": _mcp(
        "List registered investigation/chat tools wired into this OpenSRE build.",
        "User asks what tools the REPL can use",
        "User asks to list investigation or chat tools",
    ),
    "/tests": _mcp(
        "Browse and run inventoried tests from the terminal. Subcommands: list, run, synthetic.",
        "User asks to list or run bundled tests via /tests",
    ),
    "/theme": _mcp(
        "Choose and persist the interactive shell color palette (TTY picker or /theme <name>).",
        "User asks to change the REPL color theme or palette",
        anti_examples=("User asks about light/dark mode in a web UI",),
    ),
    "/trust": _mcp(
        "Enable or disable trust mode (skip execution confirmation prompts). on | off.",
        "User asks to enable or disable trust mode or auto-approve",
    ),
    "/uninstall": _mcp(
        "Remove OpenSRE and all local data from this machine. Destructive — requires confirmation.",
        "User explicitly asks to uninstall OpenSRE locally",
    ),
    "/unwatch": _mcp(
        "Cancel a running watchdog task by task id. Requires confirmation.",
        "User asks to stop a /watch background task by id",
    ),
    "/update": _mcp(
        "Check for a newer OpenSRE version and update if available.",
        "User asks to update or upgrade OpenSRE",
    ),
    "/verbose": _mcp(
        "Toggle verbose logging in the REPL. on | off.",
        "User asks to enable or disable verbose logging",
    ),
    "/version": _mcp(
        "Print OpenSRE version, Python version, and OS information.",
        "User asks for version information",
    ),
    "/watch": _mcp(
        "Watch a process by PID and send Telegram threshold alarms. Requires confirmation.",
        "User asks to watch a process or set resource threshold alarms",
    ),
    "/watchdog": _mcp(
        "Monitor one process and send threshold alarms (CLI parity wrapper).",
        "User asks to run the watchdog monitor CLI from the REPL",
    ),
    "/watches": _mcp(
        "List active watchdog background tasks with latest resource samples.",
        "User asks to list running watchdog watches",
    ),
    "/debug": _mcp(
        "Run targeted runtime diagnostics (e.g. /debug sentry to trigger a Sentry smoke test).",
        "User asks to run a debug check or diagnostic",
        anti_examples=("User asks a general debugging or troubleshooting question",),
    ),
}


def _resolve_mcp_fields(command: SlashCommand) -> _SlashMcpFields:
    registry = _MCP_BY_COMMAND.get(command.name)
    llm_description = (
        command.llm_description or (registry.llm_description if registry else "")
    ).strip()
    if not llm_description:
        llm_description = command.description.strip()
        if command.usage:
            llm_description = f"{llm_description} Common forms: {', '.join(command.usage[:3])}."

    use_cases = command.use_cases or (registry.use_cases if registry else ())
    if not use_cases:
        use_cases = (f"User intent matches: {command.description.rstrip('.')}",)

    anti_examples = command.anti_examples or (registry.anti_examples if registry else ())
    return _SlashMcpFields(
        llm_description=llm_description,
        use_cases=use_cases,
        anti_examples=anti_examples,
    )


def _derive_args_schema(command: SlashCommand) -> dict[str, Any] | None:
    if command.args_schema is not None:
        return command.args_schema
    if not command.first_arg_completions:
        return None
    hints = "; ".join(f"{keyword} ({label})" for keyword, label in command.first_arg_completions)
    return {
        "type": "array",
        "items": {"type": "string"},
        "description": (
            f"Positional arguments after {command.name}. First-argument options: {hints}."
        ),
    }


def spec_from_command(command: SlashCommand) -> SlashCommandSpec:
    mcp = _resolve_mcp_fields(command)
    return SlashCommandSpec(
        name=command.name,
        description=command.description,
        llm_description=mcp.llm_description,
        use_cases=mcp.use_cases,
        anti_examples=mcp.anti_examples,
        usage=command.usage,
        examples=command.examples,
        execution_tier=command.execution_tier,
        args_schema=_derive_args_schema(command),
    )


def build_slash_command_specs(
    commands: dict[str, SlashCommand] | None = None,
) -> list[SlashCommandSpec]:
    from cli.interactive_shell.command_registry import SLASH_COMMANDS

    source = commands if commands is not None else SLASH_COMMANDS
    return [spec_from_command(source[name]) for name in sorted(source.keys())]


def format_slash_catalog_text(
    specs: list[SlashCommandSpec] | None = None,
    *,
    compact: bool = False,
) -> str:
    entries = specs if specs is not None else build_slash_command_specs()
    if not entries:
        return ""

    lines: list[str] = []
    for spec in entries:
        desc = spec.llm_description
        if compact and len(desc) > _MAX_COMPACT_DESC_CHARS:
            desc = desc[: _MAX_COMPACT_DESC_CHARS - 1].rstrip() + "…"
        lines.append(f"- **{spec.name}** — {desc}")
        if spec.use_cases and not compact:
            lines.append(f"  - use when: {spec.use_cases[0]}")
        if spec.anti_examples and not compact:
            lines.append(f"  - not for: {spec.anti_examples[0]}")
        if spec.usage and not compact:
            lines.append(f"  - usage: {', '.join(spec.usage[:2])}")
    return "\n".join(lines)


def slash_invoke_tool_description(specs: list[SlashCommandSpec] | None = None) -> str:
    entries = specs if specs is not None else build_slash_command_specs()
    header = (
        "Run a slash command in the OpenSRE interactive shell. "
        "Pick the command whose use-case best matches the user request, then supply "
        "positional args in the args array."
    )
    # Keep planner payload intentionally tiny for live LLM runs with strict
    # prompt budgets. The full rich catalog remains available via
    # format_slash_catalog_text(..., compact=False).
    body = "\n".join(f"- `{spec.name}`" for spec in entries)
    return f"{header}\n\n{body}"


def slash_invoke_input_schema(
    specs: list[SlashCommandSpec] | None = None,
) -> dict[str, Any]:
    entries = specs if specs is not None else build_slash_command_specs()
    command_names = tuple(spec.name for spec in entries)
    args_description = (
        "Positional arguments after the command name. Valid values depend on the "
        "chosen command — see the slash_invoke tool description. Examples: "
        '["list"] for /tools, ["verify", "datadog"] for /integrations.'
    )
    return object_schema(
        properties={
            "command": string_property(
                description="Slash command name including leading `/`.",
                enum=command_names,
            ),
            "args": string_array_property(description=args_description),
        },
        required=("command",),
    )


__all__ = [
    "SlashCommandSpec",
    "build_slash_command_specs",
    "format_slash_catalog_text",
    "slash_invoke_input_schema",
    "slash_invoke_tool_description",
    "spec_from_command",
]
