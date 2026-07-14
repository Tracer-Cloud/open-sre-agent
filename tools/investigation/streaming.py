"""Helpers for JSON-safe pipeline stream event payloads."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class InvestigationPipelineStreamError(Exception):
    """Pipeline failure with loop metrics for the caller's thread context."""

    cause: BaseException
    loop_count: int
    iteration_cap: int

    def __str__(self) -> str:
        return str(self.cause)


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
