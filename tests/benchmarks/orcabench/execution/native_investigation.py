"""Unmodified OpenSRE investigation lifecycle used by the native ORCA mode."""

from __future__ import annotations

import re
from typing import Any


_TIME_SENSITIVE_GRAFANA_TOOLS = frozenset(
    {
        "query_grafana_annotations",
        "query_grafana_logs",
        "query_grafana_metrics",
        "query_grafana_traces",
    }
)
_ORCA_REPORT_HEADINGS = ("Summary", "Timeline", "5 Whys", "Remediation")
_ORCA_HEALTHY_DISPOSITION_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*\*)?root cause category(?:\*\*)?\s*[:=]\s*"
    r"(?:`|\*\*)?healthy\b",
    re.IGNORECASE | re.MULTILINE,
)
_ORCA_CONCLUSION_FORMAT_NUDGE = (
    "Your conclusion does not satisfy the ORCA report contract supplied in the system "
    "prompt. If an incident occurred, rewrite it with the required `## Summary`, "
    "`## Timeline`, `## 5 Whys`, and `## Remediation` sections. If no incident "
    "occurred, state `Root cause category: healthy` and do not fabricate incident "
    "sections."
)
_NATIVE_OUTPUT_CONTRACT_HEADING = "\n## What to produce at the end\n"
_LLM_FAILURE_CAUSAL_CHAIN_PREFIX = "LLM invoke failed:"


class NativeInvestigationIncompleteError(RuntimeError):
    """OpenSRE did not reach a benchmark-scorable terminal conclusion."""


def _raise_on_llm_failure(state: dict[str, Any]) -> None:
    """Propagate OpenSRE's structured degraded state like Terminus propagates errors."""
    causal_chain = state.get("causal_chain")
    if not isinstance(causal_chain, list) or not any(
        isinstance(step, str) and step.startswith(_LLM_FAILURE_CAUSAL_CHAIN_PREFIX)
        for step in causal_chain
    ):
        return
    root_cause = state.get("root_cause")
    detail = (
        root_cause
        if isinstance(root_cause, str) and root_cause.strip()
        else "unknown error"
    )
    raise NativeInvestigationIncompleteError(
        f"OpenSRE investigation did not complete because an LLM invocation failed: {detail}"
    )


def _orca_report_contract(state: dict[str, Any]) -> tuple[str, str]:
    """Return task guidance and report instructions kept outside alert relevance text."""
    raw_alert = state.get("raw_alert")
    meta = raw_alert.get("_meta") if isinstance(raw_alert, dict) else None
    if not isinstance(meta, dict):
        return "", ""
    guidance = meta.get("orca_investigation_guidance")
    report_instructions = meta.get("orca_report_instructions")
    return (
        guidance.strip() if isinstance(guidance, str) else "",
        report_instructions.strip() if isinstance(report_instructions, str) else "",
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
    """Append public ORCA task semantics and output contract after planning."""
    from tools.investigation.stages.gather_evidence.prompt import (
        build_investigation_system_prompt,
    )

    base = build_investigation_system_prompt(state)
    guidance, report_instructions = _orca_report_contract(state)
    if report_instructions:
        base, separator, _native_output_contract = base.partition(
            _NATIVE_OUTPUT_CONTRACT_HEADING
        )
        if not separator:
            raise RuntimeError("OpenSRE investigation output contract heading is missing")
        base = base.replace(
            ' (see "What to produce at the end")',
            " using the ORCA report contract below",
        ).rstrip()
    additions: list[str] = []
    if guidance:
        additions.append(f"## ORCA task guidance\n\n{guidance}")
    if report_instructions:
        additions.append(
            "## ORCA report contract\n\n"
            "Your final assistant message is the candidate contents of `/app/report.md`. "
            "Follow the benchmark's report instructions below. When no incident occurred, "
            "state `Root cause category: healthy`; the benchmark adapter will write the "
            "required empty file.\n\n"
            f"{report_instructions}"
        )
    if not additions:
        return base
    return f"{base}\n\n" + "\n\n".join(additions)


def _orca_conclusion_complete(text: str) -> bool:
    """Accept either an explicit healthy disposition or all ORCA report sections."""
    if not text.strip():
        return False
    if _ORCA_HEALTHY_DISPOSITION_RE.search(text):
        return True
    return all(
        re.search(
            rf"^##\s+(?:Section\s+\d+\s*:\s*)?{re.escape(heading)}\s*$",
            text,
            re.IGNORECASE | re.MULTILINE,
        )
        for heading in _ORCA_REPORT_HEADINGS
    )


def _assistant_message_has_tool_calls(message: dict[str, Any]) -> bool:
    if message.get("tool_calls"):
        return True
    content = message.get("content")
    return isinstance(content, list) and any(
        isinstance(block, dict)
        and (block.get("type") == "tool_use" or "toolUse" in block)
        for block in content
    )


def _terminal_orca_conclusion(state: dict[str, Any]) -> str:
    """Return the valid tool-free conclusion or fail the unscorable run."""
    from core.messages.transcript import extract_last_assistant_text

    raw_messages = state.get("agent_messages")
    messages = raw_messages if isinstance(raw_messages, list) else []
    terminal = messages[-1] if messages and isinstance(messages[-1], dict) else None
    conclusion = ""
    if (
        terminal is not None
        and terminal.get("role") == "assistant"
        and not _assistant_message_has_tool_calls(terminal)
    ):
        conclusion = extract_last_assistant_text([terminal])
    if conclusion and _orca_conclusion_complete(conclusion):
        return conclusion

    loop_count = state.get("investigation_loop_count")
    iteration_cap = state.get("investigation_iteration_cap")
    if (
        isinstance(loop_count, int)
        and isinstance(iteration_cap, int)
        and iteration_cap > 0
        and loop_count >= iteration_cap
    ):
        raise NativeInvestigationIncompleteError(
            "OpenSRE investigation reached its iteration cap without a valid "
            "terminal ORCA conclusion"
        )
    raise NativeInvestigationIncompleteError(
        "OpenSRE investigation ended without a valid terminal ORCA conclusion"
    )


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

            def _should_accept_conclusion(
                self,
                *,
                evidence_count: int,  # noqa: ARG002 - shared agent hook
                iteration: int,  # noqa: ARG002 - shared agent hook
                final_text: str = "",
            ) -> tuple[bool, str | None]:
                text = final_text or getattr(self, "_last_assistant_text", "") or ""
                if _orca_conclusion_complete(text):
                    return True, None
                if not getattr(self, "_conclusion_format_nudged", False):
                    self._conclusion_format_nudged = True
                    return False, _ORCA_CONCLUSION_FORMAT_NUDGE
                return True, None

        install_harness_ports()
        state = run_investigation(
            alert,
            resolved_integrations=integrations,
            incident_window=incident_window,
            agent_class=OrcaInvestigationAgent,
        )
        return dict(state)

    def build_payload(self, state: dict) -> dict[str, Any]:
        """Project native state into ORCA's disposition and report contract."""
        from tools.investigation.capability import build_investigation_payload

        _raise_on_llm_failure(state)
        conclusion = _terminal_orca_conclusion(state)
        payload = build_investigation_payload(state)
        payload["report"] = conclusion
        payload["root_cause_category"] = (
            "healthy"
            if _ORCA_HEALTHY_DISPOSITION_RE.search(conclusion)
            else state.get("root_cause_category", "")
        )
        return payload
