"""Evidence tracing model for the investigation ReAct loop."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class EvidenceEntry(BaseModel):
    """Provenance record for a single tool call in the agent loop."""

    key: str
    data: Any
    tool_name: str = ""
    tool_args: dict[str, Any] = Field(default_factory=dict)
    source: str = "unknown"
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    loop_iteration: int = 0
    confidence: float | None = None
