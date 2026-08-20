"""Type definitions and contracts for background investigation runners."""

from __future__ import annotations

from typing import Any, Protocol, TypedDict


class BackgroundRunResult(TypedDict, total=False):
    """Result payload produced by a background investigation run."""

    root_cause: str
    validated_claims: list[dict[str, Any]]
    remediation_steps: list[str]
    evidence_entries: list[Any]
    investigation_loop_count: int
    validity_score: float


class BackgroundRunFn(Protocol):
    """Callable contract for executing a background investigation."""

    def __call__(self, *args: Any, **kwargs: Any) -> BackgroundRunResult:
        """Run the background investigation and return its result payload."""
