"""Apply partial stage updates to pipeline state."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from context.state.models import AgentState


def apply_state_updates(state: AgentState, updates: Mapping[str, Any] | None) -> None:
    """Apply partial state updates using per-key reducer semantics.

    Most keys last-write-wins. ``messages`` appends (list extend or single append).
    """
    if not updates:
        return
    target = cast(dict[str, Any], state)
    for key, value in updates.items():
        if key == "messages":
            messages = list(target.get("messages") or [])
            if isinstance(value, list):
                messages.extend(value)
            else:
                messages.append(value)
            target["messages"] = messages
        else:
            target[key] = value


__all__ = ["apply_state_updates"]
