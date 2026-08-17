"""Small variation-point contracts for ORCA execution collaborators."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

class ConnectionProvider(Protocol):
    def build(
        self,
        environ: dict[str, str],
        incident_window: dict[str, Any],
    ) -> dict[str, Any]:
        """Build connection-only OpenSRE resolved integrations."""


class InvestigationRunner(Protocol):
    def investigate(
        self,
        alert: str | dict[str, Any],
        integrations: dict[str, Any],
        incident_window: dict[str, Any],
    ) -> dict:
        """Run one investigation and return its native state."""

    def build_payload(self, state: dict) -> dict[str, Any]:
        """Project native state through OpenSRE's public payload builder."""


class ReportPolicy(Protocol):
    def write(self, payload: dict[str, Any], destination: Path) -> bytes:
        """Persist the selected report and return the exact written bytes."""
