"""Security alert remediation action tool."""

from __future__ import annotations

from typing import Any

from cli.interactive_shell.harness.orchestration.action_executor import (
    run_claude_code_security_fix_pr,
)
from cli.interactive_shell.harness.orchestration.execution_tier import (
    ExecutionTier,
)
from cli.interactive_shell.harness.orchestration.tool_contracts import (
    ToolContext,
    ToolEntry,
    capability_not_explicitly_disabled,
    object_schema,
    string_property,
)


def execute_security_fix_pr_action(args: dict[str, Any], ctx: ToolContext) -> bool:
    alerts_url = str(args.get("alerts_url", "")).strip()
    instructions = str(args.get("instructions", "")).strip()
    if not alerts_url:
        return False
    run_claude_code_security_fix_pr(
        alerts_url,
        instructions,
        ctx.session,
        ctx.console,
        confirm_fn=ctx.confirm_fn,
        is_tty=ctx.is_tty,
        action_already_listed=ctx.action_already_listed,
    )
    return True


TOOL_ENTRY = ToolEntry(
    name="code_fix_security_alerts",
    description=(
        "Launch a local Claude Code agent to fix GitHub code-scanning/security "
        "alerts and open a pull request."
    ),
    input_schema=object_schema(
        properties={
            "alerts_url": string_property(
                description="GitHub code-scanning or security-alerts URL to inspect.",
                min_length=1,
            ),
            "instructions": string_property(
                description="Optional extra remediation or PR instructions.",
            ),
        },
        required=("alerts_url",),
    ),
    execution_tier=ExecutionTier.ELEVATED,
    execute=execute_security_fix_pr_action,
    is_available=lambda session: capability_not_explicitly_disabled(session, "implementation"),
)


__all__ = ["TOOL_ENTRY", "execute_security_fix_pr_action"]
