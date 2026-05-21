"""Parse planner tool-call payloads into structured planned actions."""

from __future__ import annotations

import json
from typing import Any

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
    default_target_surface,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.tool_registry import (
    ACTION_KIND_TO_TOOL,
    REGISTRY,
)
from app.cli.interactive_shell.runtime.session import ReplSession

from .constants import _UNHANDLED_MARKER
from .normalization import _content_from_tool_args, _normalize_tool_args

_TOOL_TO_ACTION_KIND = {tool: kind for kind, tool in ACTION_KIND_TO_TOOL.items()}


def _parse_tool_plan(
    raw: str,
    *,
    session: Any | None = None,
) -> tuple[list[PlannedAction], bool] | None:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return [], bool(raw.strip())

    if not isinstance(data, dict):
        return None

    raw_calls = data.get("tool_calls")
    text = str(data.get("text", "")).strip()
    has_unhandled = text.startswith(_UNHANDLED_MARKER)
    if not isinstance(raw_calls, list):
        return [], bool(text)

    if not has_unhandled:
        for call in raw_calls:
            if not isinstance(call, dict):
                continue
            call_name = str(call.get("name", "")).strip()
            if call_name == "mark_unhandled":
                has_unhandled = True
                break
            if call_name == "assistant_handoff":
                call_args = call.get("arguments")
                if isinstance(call_args, dict) and str(
                    call_args.get("content", "")
                ).lstrip().startswith(_UNHANDLED_MARKER):
                    has_unhandled = True
                    break

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
            has_unhandled = True
            continue

        raw_args = call.get("arguments")
        args = raw_args if isinstance(raw_args, dict) else {}
        normalized_args = _normalize_tool_args(kind, args, session=session)
        if normalized_args is None:
            has_unhandled = True
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

    return actions, has_unhandled
