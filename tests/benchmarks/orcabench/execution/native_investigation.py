"""Unmodified OpenSRE investigation lifecycle used by the native ORCA mode."""

from __future__ import annotations

from typing import Any


_TIME_SENSITIVE_GRAFANA_TOOLS = frozenset(
    {
        "query_grafana_annotations",
        "query_grafana_logs",
        "query_grafana_metrics",
        "query_grafana_traces",
    }
)


def _with_orca_time_bounds(tools: list[Any]) -> list[Any]:
    """Expose native OpenSRE time controls only on ORCA's historical backend."""
    from copy import deepcopy
    from dataclasses import replace

    from core.domain.types.retrieval import TimeBounds

    time_schema = TimeBounds.model_json_schema()
    adapted: list[Any] = []
    for tool in tools:
        if tool.name not in _TIME_SENSITIVE_GRAFANA_TOOLS:
            adapted.append(tool)
            continue
        input_schema = deepcopy(tool.input_schema)
        input_schema.setdefault("properties", {})["time_bounds"] = time_schema
        controls = tool.retrieval_controls.model_copy(update={"time_bounds": True})
        adapted.append(
            replace(
                tool,
                input_schema=input_schema,
                retrieval_controls=controls,
            )
        )
    return adapted


def _build_orca_investigation_system_prompt(state: dict[str, Any]) -> str:
    """Append public ORCA task semantics without affecting alert planning."""
    from tools.investigation.stages.gather_evidence.prompt import (
        build_investigation_system_prompt,
    )

    base = build_investigation_system_prompt(state)
    raw_alert = state.get("raw_alert")
    meta = raw_alert.get("_meta") if isinstance(raw_alert, dict) else None
    guidance = (
        meta.get("orca_investigation_guidance") if isinstance(meta, dict) else None
    )
    if not isinstance(guidance, str) or not guidance.strip():
        return base
    return f"{base}\n\n## ORCA task guidance\n\n{guidance.strip()}"


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
        from tools.investigation.stages.gather_evidence import ConnectedInvestigationAgent

        class OrcaInvestigationAgent(ConnectedInvestigationAgent):
            """Native OpenSRE agent with the caller's ORCA task semantics."""

            def _build_system_prompt(self, state: dict[str, Any]) -> str:
                return _build_orca_investigation_system_prompt(state)

            def _filter_tools(self, tools: list[Any]) -> list[Any]:
                return _with_orca_time_bounds(tools)

        install_harness_ports()
        state = run_investigation(
            alert,
            resolved_integrations=integrations,
            incident_window=incident_window,
            agent_class=OrcaInvestigationAgent,
        )
        return dict(state)

    def build_payload(self, state: dict) -> dict[str, Any]:
        """Add the structured disposition needed by ORCA's report policy."""
        from tools.investigation.capability import build_investigation_payload

        payload = build_investigation_payload(state)
        payload["root_cause_category"] = state.get("root_cause_category", "")
        return payload
