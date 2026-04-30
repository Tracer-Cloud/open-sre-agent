"""Canonical verification probe results."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProbeResult:
    """Result returned by verification-facing client probe methods."""

    status: str
    detail: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "passed"

    @classmethod
    def passed(cls, detail: str, **metadata: Any) -> ProbeResult:
        return cls(status="passed", detail=detail, metadata=metadata)

    @classmethod
    def failed(cls, detail: str, **metadata: Any) -> ProbeResult:
        return cls(status="failed", detail=detail, metadata=metadata)

    @classmethod
    def missing(cls, detail: str, **metadata: Any) -> ProbeResult:
        return cls(status="missing", detail=detail, metadata=metadata)
