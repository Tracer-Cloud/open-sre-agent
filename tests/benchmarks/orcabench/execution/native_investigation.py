"""Unmodified OpenSRE investigation lifecycle used by the native ORCA mode."""

from __future__ import annotations

from typing import Any

class NativeInvestigationRunner:
    """Bootstrap and invoke OpenSRE's public investigation capability once."""

    def investigate(
        self,
        instruction: str,
        integrations: dict[str, Any],
        incident_window: dict[str, Any],
    ) -> dict:
        """Bootstrap the normal runtime and return native AgentState."""
        from surfaces.interactive_shell.ui.output.boundary import install_harness_ports
        from tools.investigation.capability import run_investigation

        install_harness_ports()
        state = run_investigation(
            instruction,
            resolved_integrations=integrations,
            incident_window=incident_window,
        )
        return dict(state)

    def build_payload(self, state: dict) -> dict[str, Any]:
        """Use OpenSRE's standard public state projection without adaptation."""
        from tools.investigation.capability import build_investigation_payload

        return build_investigation_payload(state)
