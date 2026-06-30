"""Adapt resolved integration configs to the legacy tool availability contract."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def availability_view(resolved_integrations: dict[str, Any]) -> dict[str, Any]:
    """Convert classified integration configs into dicts tools can consume."""
    view: dict[str, Any] = {}
    for key, value in resolved_integrations.items():
        if key.startswith("_"):
            view[key] = value
            continue
        if isinstance(value, BaseModel):
            item = value.model_dump(exclude_none=True)
            item.setdefault("connection_verified", True)
            view[key] = item
        elif isinstance(value, dict) and value:
            item = dict(value)
            item.setdefault("connection_verified", True)
            view[key] = item
        else:
            view[key] = value
    return view
