from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BenchmarkCase:
    """Framework-neutral case metadata used by benchmark adapters."""

    case_id: str
    payload: Any
    tags: dict[str, Any] = field(default_factory=dict)


class BenchmarkAdapter(ABC):
    """Contract implemented by benchmark-specific adapters."""

    name: str
    version: str

    @abstractmethod
    def load_cases(self, filters: dict[str, Any]) -> Iterable[BenchmarkCase]:
        """Load cases matching framework-level filters."""

    @abstractmethod
    def run_case(self, case: BenchmarkCase, output_dir: str) -> dict[str, Any]:
        """Execute one OpenSRE run and return a serializable run payload."""

    @abstractmethod
    def score_case(self, case: BenchmarkCase, run_result: dict[str, Any]) -> dict[str, Any]:
        """Score one run result using the adapter's benchmark metrics."""

    @abstractmethod
    def metric_schema(self) -> dict[str, Any]:
        """Return metric metadata for reports and comparison tooling."""
