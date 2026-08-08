"""Unmodified OpenSRE investigation lifecycle used by the native ORCA mode."""

from __future__ import annotations

from typing import Any

class NativeInvestigationRunner:
    """Bootstrap and invoke OpenSRE's public investigation capability once."""

    def investigate(
        self,
        alert: str | dict[str, Any],
        integrations: dict[str, Any],
        incident_window: dict[str, Any],
    ) -> dict:
        """Bootstrap the normal runtime and return native AgentState."""
        from surfaces.interactive_shell.ui.output.boundary import install_harness_ports
        from tools.investigation.capability import run_investigation

        install_harness_ports()
        state = run_investigation(
            alert,
            resolved_integrations=integrations,
            incident_window=incident_window,
        )
        return dict(state)

    def build_payload(self, state: dict) -> dict[str, Any]:
        """Add the structured disposition needed by ORCA's report policy."""
        from tools.investigation.capability import build_investigation_payload

        payload = build_investigation_payload(state)
        payload["root_cause_category"] = state.get("root_cause_category", "")
        return payload
