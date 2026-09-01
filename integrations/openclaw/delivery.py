"""OpenClaw MCP write-back helpers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from integrations.openclaw import (
    build_openclaw_config,
    call_openclaw_tool,
    describe_openclaw_error,
    openclaw_runtime_unavailable_reason,
)

if TYPE_CHECKING:
    from core.state import InvestigationState

logger = logging.getLogger(__name__)

_OVERRIDABLE_KEYS = ("url", "mode", "auth_token", "command", "args", "headers", "timeout_seconds")
MISSING_CONVERSATION_ID = (
    "OpenClaw write-back requires openclaw_conversation_id; "
    "creating a new conversation is not supported."
)


def _report_body(state: InvestigationState, report: str) -> str:
    sections = [report.strip()]
    root_cause = str(state.get("root_cause") or "").strip()
    if root_cause:
        sections.append(f"Root cause: {root_cause}")

    remediation_steps = state.get("remediation_steps") or []
    if remediation_steps:
        rendered_steps = "\n".join(f"- {step}" for step in remediation_steps if str(step).strip())
        if rendered_steps:
            sections.append(f"Remediation steps:\n{rendered_steps}")

    validity_score = state.get("validity_score")
    if isinstance(validity_score, (int, float)):
        sections.append(f"Confidence: {validity_score:.0%}")

    return "\n\n".join(section for section in sections if section).strip()


def _merge_openclaw_credentials(
    creds: dict[str, Any],
    openclaw_context: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(creds)
    for key in _OVERRIDABLE_KEYS:
        if key not in openclaw_context:
            continue
        merged[key] = openclaw_context[key]
    return merged


def _send_message(
    config_payload: dict[str, Any],
    arguments: dict[str, Any],
) -> tuple[bool, str | None]:
    config = build_openclaw_config(config_payload)
    runtime_error = openclaw_runtime_unavailable_reason(config)
    if runtime_error:
        return False, runtime_error

    try:
        result = call_openclaw_tool(config, "messages_send", arguments)
        if result.get("is_error"):
            return False, str(result.get("text") or "OpenClaw tool call failed.")
        return True, None
    except Exception as exc:
        return False, describe_openclaw_error(exc, config)


def send_openclaw_report(
    state: InvestigationState,
    report: str,
    creds: dict[str, Any],
) -> tuple[bool, str | None]:
    """Write the investigation report to OpenClaw via MCP."""
    from core.state.channel_context import get_channel_context

    openclaw_context = get_channel_context(state, "openclaw")
    merged_creds = _merge_openclaw_credentials(creds, openclaw_context)
    config_payload = {
        "url": merged_creds.get("url", ""),
        "mode": merged_creds.get("mode", "streamable-http"),
        "auth_token": merged_creds.get("auth_token", ""),
        "command": merged_creds.get("command", ""),
        "args": merged_creds.get("args", []),
        "headers": merged_creds.get("headers", {}),
        "timeout_seconds": merged_creds.get("timeout_seconds", 15.0),
    }

    try:
        build_openclaw_config(config_payload)
    except Exception as exc:
        return False, f"OpenClaw config invalid: {exc}"

    content = _report_body(state, report)
    conversation_id = str(
        openclaw_context.get("conversation_id")
        or merged_creds.get("openclaw_conversation_id")
        or merged_creds.get("conversation_id")
        or ""
    ).strip()
    if not conversation_id:
        return False, MISSING_CONVERSATION_ID

    posted, error = _send_message(
        config_payload,
        {"session_key": conversation_id, "text": content},
    )
    if posted:
        return True, None
    logger.debug("[openclaw_delivery] messages_send failed: %s", error)
    return False, error
