"""Canonical verification probe results."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ProbeStatus(StrEnum):
    """Closed set of integration verification probe outcomes."""

    PASSED = "passed"
    FAILED = "failed"
    MISSING = "missing"


@dataclass(frozen=True)
class ProbeResult:
    """Result returned by verification-facing client probe methods."""

    status: ProbeStatus
    detail: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", ProbeStatus(self.status))

    @property
    def ok(self) -> bool:
        return self.status is ProbeStatus.PASSED

    @classmethod
    def passed(cls, detail: str, **metadata: Any) -> ProbeResult:
        return cls(status=ProbeStatus.PASSED, detail=detail, metadata=metadata)

    @classmethod
    def failed(cls, detail: str, **metadata: Any) -> ProbeResult:
        return cls(status=ProbeStatus.FAILED, detail=detail, metadata=metadata)

    @classmethod
    def missing(cls, detail: str, **metadata: Any) -> ProbeResult:
        return cls(status=ProbeStatus.MISSING, detail=detail, metadata=metadata)
