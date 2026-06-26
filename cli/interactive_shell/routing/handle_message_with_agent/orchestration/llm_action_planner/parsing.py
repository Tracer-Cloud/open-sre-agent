"""Parse planner tool-call payloads into structured planned actions."""

from __future__ import annotations

import json
from typing import Any

from cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
    default_target_surface,
)
from cli.interactive_shell.routing.handle_message_with_agent.orchestration.tool_registry import (
    ACTION_KIND_TO_TOOL,
    REGISTRY,
)
from cli.interactive_shell.runtime.session import ReplSession

from .normalization import _content_from_tool_args, _normalize_tool_args

_TOOL_TO_ACTION_KIND = {tool: kind for kind, tool in ACTION_KIND_TO_TOOL.items()}


def _parse_tool_plan(
    raw: str,
    *,
    session: Any | None = None,
) -> tuple[list[PlannedAction], bool] | None:
    """Parse planner output into executable actions.

    The second tuple element (``has_unhandled``) is retained for back-compat with
    callers but is always ``False``: v0.1 has no planning-stage fail-closed
    safeguard, so unmapped or unavailable tool calls are simply dropped rather
    than marked unhandled. Anything not mapped to an executable action falls
    through to the conversational assistant.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return [], False

    if not isinstance(data, dict):
        return None

    raw_calls = data.get("tool_calls")
    if not isinstance(raw_calls, list):
        return [], False

    actions: list[PlannedAction] = []
    session_for_availability = session if isinstance(session, ReplSession) else ReplSession()
    for idx, call in enumerate(raw_calls):
        if not isinstance(call, dict):
            continue
        tool_name = str(call.get("name", "")).strip()
        kind = _TOOL_TO_ACTION_KIND.get(tool_name)
        if kind is None:
            continue
        entry = REGISTRY.get(tool_name)
        if entry is None or not entry.is_available(session_for_availability):
            continue

        raw_args = call.get("arguments")
        args = raw_args if isinstance(raw_args, dict) else {}
        normalized_args = _normalize_tool_args(kind, args, session=session)
        if normalized_args is None:
            continue

        actions.append(
            PlannedAction(
                kind=kind,  # type: ignore[arg-type]
                content=_content_from_tool_args(kind, normalized_args),
                position=idx,
                source="llm",
                confidence=1.0,
                rationale=None,
                target_surface=default_target_surface(kind),  # type: ignore[arg-type]
                args=normalized_args,
            )
        )

    return actions, False
