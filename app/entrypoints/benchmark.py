"""Benchmark output mode — emits Cloud-OpsBench-style results from the pipeline.

Wraps the standard investigation runner and formats its final state into a
ranked-prediction record suitable for offline scoring against labeled fault
cases. The default investigation output is unchanged.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.investigation_constants import MAX_INVESTIGATION_LOOPS

StopReason = Literal[
    "final_answer",
    "max_loops",
    "no_actions_available",
    "error",
    "unknown",
]


class Prediction(BaseModel):
    rank: int
    fault_taxonomy: str
    fault_object: str
    root_cause: str


class BenchmarkResult(BaseModel):
    model: str
    fault_category: str
    prompt_strategy: str = Field(alias="prompt_strat")
    case_name: str
    case_path: str
    finished: bool
    stop_reason: StopReason
    steps_used: int
    elapsed_seconds: float
    trace_path: str = "/"
    result_path: str = "/"
    key_evidence_summary: str
    top_3_predictions: list[Prediction]

    model_config = {"populate_by_name": True}


@dataclass
class BenchmarkInputs:
    """Caller-supplied metadata that the pipeline itself does not own."""

    case_name: str
    case_path: str
    fault_category: str = ""
    model: str = ""
    prompt_strategy: str = "base"
    trace_path: str = "/"
    result_path: str = "/"
    raw_alert: str | dict[str, Any] | None = None
    alert_name: str = "Incident"
    pipeline_name: str = "events_fact"
    severity: str = "warning"
    resolved_integrations: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _derive_stop_reason(state: dict[str, Any], errored: bool) -> StopReason:
    if errored:
        return "error"
    if state.get("root_cause"):
        return "final_answer"
    if state.get("investigation_loop_count", 0) > MAX_INVESTIGATION_LOOPS:
        return "max_loops"
    if not state.get("available_action_names"):
        return "no_actions_available"
    return "unknown"


def _summarize_evidence(state: dict[str, Any]) -> str:
    """Concatenate validated claims into a short evidence blurb. No LLM call."""
    claims = state.get("validated_claims") or []
    parts: list[str] = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        text = (
            claim.get("claim")
            or claim.get("statement")
            or claim.get("description")
            or claim.get("text")
            or ""
        )
        text = str(text).strip()
        if text:
            parts.append(text)
    if parts:
        return " ".join(parts)
    return str(state.get("root_cause") or "").strip()


def _resolve_fault_object(state: dict[str, Any]) -> str:
    alert = state.get("alert_json") or {}
    if isinstance(alert, dict):
        for key in ("fault_object", "service", "resource", "pod", "deployment"):
            value = alert.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return str(state.get("alert_name") or "").strip()


def _build_predictions(state: dict[str, Any]) -> list[Prediction]:
    category = str(state.get("root_cause_category") or "").strip()
    fault_object = _resolve_fault_object(state)
    root_cause = str(state.get("root_cause") or "").strip()

    predictions: list[Prediction] = []
    if category or root_cause:
        predictions.append(
            Prediction(
                rank=1,
                fault_taxonomy=category or "unknown",
                fault_object=fault_object,
                root_cause=root_cause or "unknown",
            )
        )

    hypotheses = state.get("hypotheses") or []
    for hypo in hypotheses:
        if len(predictions) >= 3:
            break
        text = str(hypo).strip() if hypo is not None else ""
        if not text:
            continue
        predictions.append(
            Prediction(
                rank=len(predictions) + 1,
                fault_taxonomy=category or "unknown",
                fault_object=fault_object,
                root_cause=text,
            )
        )

    return predictions


def _resolve_model(explicit: str) -> str:
    if explicit:
        return explicit
    try:
        from app.config import LLMSettings

        settings = LLMSettings.from_env()
        provider = settings.provider
        attr = f"{provider}_reasoning_model"
        return getattr(settings, attr, provider)
    except Exception:
        return "unknown"


def run_benchmark_case(inputs: BenchmarkInputs) -> BenchmarkResult:
    """Run the investigation pipeline and return a benchmark-formatted result."""
    from app.pipeline.runners import run_investigation

    started = time.monotonic()
    errored = False
    state: dict[str, Any] = {}
    try:
        state = dict(
            run_investigation(
                inputs.alert_name,
                inputs.pipeline_name,
                inputs.severity,
                raw_alert=inputs.raw_alert,
                resolved_integrations=inputs.resolved_integrations,
            )
        )
    except Exception:
        errored = True
    elapsed = time.monotonic() - started

    stop_reason = _derive_stop_reason(state, errored)
    return BenchmarkResult(
        model=_resolve_model(inputs.model),
        fault_category=inputs.fault_category or str(state.get("root_cause_category") or ""),
        prompt_strat=inputs.prompt_strategy,
        case_name=inputs.case_name,
        case_path=inputs.case_path,
        finished=stop_reason == "final_answer",
        stop_reason=stop_reason,
        steps_used=int(state.get("investigation_loop_count", 0)),
        elapsed_seconds=round(elapsed, 3),
        trace_path=inputs.trace_path,
        result_path=inputs.result_path,
        key_evidence_summary=_summarize_evidence(state),
        top_3_predictions=_build_predictions(state),
    )


def format_benchmark_block(result: BenchmarkResult) -> str:
    """Render a BenchmarkResult in the Cloud-OpsBench plaintext layout."""
    payload = {
        "key_evidence_summary": result.key_evidence_summary,
        "top_3_predictions": [p.model_dump() for p in result.top_3_predictions],
    }
    import json

    lines = [
        f"Model         : {result.model}",
        f"Fault Category: {result.fault_category}",
        f"Prompt Strat. : {result.prompt_strategy}",
        f"Case Name     : {result.case_name}",
        f"Case Path     : {result.case_path}",
        f"Finished      : {result.finished}",
        f"Stop Reason   : {result.stop_reason}",
        f"Steps Used    : {result.steps_used}",
        f"Trace Path    : {result.trace_path}",
        f"Result Path   : {result.result_path}",
        "Final Answer:",
        json.dumps(payload),
    ]
    return "\n".join(lines)
