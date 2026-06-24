"""Helpers for JSON-safe pipeline stream event payloads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel


def normalize_stream_payload(value: Any) -> Any:
    """Recursively convert typed configs into JSON-serializable values."""
    if isinstance(value, BaseModel):
        return normalize_stream_payload(value.model_dump(exclude_none=True))
    if isinstance(value, Mapping):
        return {str(key): normalize_stream_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [normalize_stream_payload(item) for item in value]
    return value


def resolved_integrations_stream_payload(resolved: Mapping[str, Any]) -> dict[str, Any]:
    """Return resolved integrations without raw records and without live models."""
    return {
        str(key): normalize_stream_payload(value)
        for key, value in resolved.items()
        if key != "_all"
    }
