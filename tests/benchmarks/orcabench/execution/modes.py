"""Execution-mode composition without mode conditionals in the runner."""

from __future__ import annotations

from dataclasses import dataclass

from tests.benchmarks.orcabench.config import RunnerSettings
from tests.benchmarks.orcabench.execution.contracts import (
    ConnectionProvider,
    InvestigationRunner,
    ReportPolicy,
)
from tests.benchmarks.orcabench.execution.native_connection import OrcaNativeConnections
from tests.benchmarks.orcabench.execution.native_investigation import (
    NativeInvestigationRunner,
)
from tests.benchmarks.orcabench.execution.native_report import NativeReportPolicy


@dataclass(frozen=True)
class ModeComponents:
    """Concrete collaborators selected for one execution mode."""

    settings: RunnerSettings
    connections: ConnectionProvider
    investigation: InvestigationRunner
    report: ReportPolicy

    @property
    def name(self) -> str:
        """Return the configured stable mode name."""
        return self.settings.benchmark.mode


def build_mode(settings: RunnerSettings) -> ModeComponents:
    """Compose the requested mode; unsupported modes fail during settings validation."""
    return ModeComponents(
        settings=settings,
        connections=OrcaNativeConnections(
            settings.benchmark.grafana,
            settings.benchmark.runtime.source_root,
        ),
        investigation=NativeInvestigationRunner(),
        report=NativeReportPolicy(),
    )
