"""LLM provider switch action tool."""

from __future__ import annotations

from typing import Any

from rich.markup import escape

from app.cli.interactive_shell.command_registry import switch_llm_provider, switch_reasoning_model
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_policy import (
    evaluate_llm_runtime_switch,
    execution_allowed,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.execution_tier import (
    ExecutionTier,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.tool_contracts import (
    ToolContext,
    ToolEntry,
    capability_not_explicitly_disabled,
    object_schema,
)


def _provider_values() -> tuple[str, ...]:
    from app.cli.wizard.config import PROVIDER_BY_VALUE

    return tuple(sorted(PROVIDER_BY_VALUE.keys()))


def _target_property_schema() -> dict[str, Any]:
    provider_values = _provider_values()
    provider_list = ", ".join(provider_values)
    return {
        "description": (
            "Target passed to `/model set <target>`. Use one of the provider names "
            f"({provider_list}) to switch providers, or pass a valid reasoning model "
            "name for the active provider."
        ),
        "oneOf": [
            {"type": "string", "enum": list(provider_values)},
            {"type": "string", "minLength": 1},
        ],
    }


def _apply_model_set_target(target: str, ctx: ToolContext) -> bool:
    from app.cli.wizard.config import PROVIDER_BY_VALUE

    candidate = target.strip()
    if candidate.lower() in PROVIDER_BY_VALUE:
        return switch_llm_provider(candidate, ctx.console)
    return switch_reasoning_model(candidate, ctx.console)


def execute_llm_provider_action(args: dict[str, Any], ctx: ToolContext) -> bool:
    target = str(args.get("target", args.get("provider", ""))).strip()
    if not target:
        return False
    policy = evaluate_llm_runtime_switch(action_type="switch_llm_provider")
    if not execution_allowed(
        policy,
        session=ctx.session,
        console=ctx.console,
        action_summary=f"/model set {target}",
        confirm_fn=ctx.confirm_fn,
        is_tty=ctx.is_tty,
        action_already_listed=ctx.action_already_listed,
    ):
        return True
    ctx.console.print(f"[bold]$ /model set {escape(target)}[/bold]")
    ok = _apply_model_set_target(target, ctx)
    ctx.session.record("slash", f"/model set {target}", ok=ok)
    return True


TOOL_ENTRY = ToolEntry(
    name="llm_set_provider",
    description="Switch the active LLM provider or reasoning model.",
    input_schema=object_schema(
        properties={"target": _target_property_schema()},
        required=("target",),
    ),
    execution_tier=ExecutionTier.ELEVATED,
    execute=execute_llm_provider_action,
    is_available=lambda session: capability_not_explicitly_disabled(session, "llm_provider"),
)


__all__ = ["TOOL_ENTRY", "execute_llm_provider_action"]
