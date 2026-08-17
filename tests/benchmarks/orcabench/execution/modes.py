"""OpenSRE native execution composition without runner conditionals."""

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
class ExecutionComponents:
    """Concrete collaborators for the OpenSRE native execution path."""

    settings: RunnerSettings
    connections: ConnectionProvider
    investigation: InvestigationRunner
    report: ReportPolicy


def build_mode(settings: RunnerSettings) -> ExecutionComponents:
    """Compose the OpenSRE native runner with the configured tool surface."""
    return ExecutionComponents(
        settings=settings,
        connections=OrcaNativeConnections(
            settings.benchmark.grafana,
            settings.benchmark.runtime.source_root,
            tool_capability_mode=settings.benchmark.tool_capability_mode,
        ),
        investigation=NativeInvestigationRunner(
            tool_capability_mode=settings.benchmark.tool_capability_mode,
        ),
        report=NativeReportPolicy(),
    )
