"""Tracer Airflow DAG/task investigation tools."""

from __future__ import annotations

import os
from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from infrastructure.text.truncation import truncate
from integrations.airflow.config import (
    AirflowConfig,
    build_airflow_config,
)
from integrations.airflow.config import (
    get_airflow_dag_runs as fetch_airflow_dag_runs,
)
from integrations.airflow.config import (
    get_airflow_task_instances as fetch_airflow_task_instances,
)
from integrations.airflow.config import (
    get_recent_airflow_failures as fetch_recent_airflow_failures,
)

#: These tools have no ``available``/success flag -- ``{"error": ...}`` is the
#: only failure signal, and a request failure (auth, network, 5xx) inside the
#: config-layer fetch functions is swallowed into an empty list rather than
#: surfaced. A mapper therefore cannot distinguish "genuinely zero results"
#: from "the fetch silently failed", so it stays silent on empty lists rather
#: than assert a false zero -- the same conservative choice made throughout
#: this codebase's other evidence mappers.
_ID_SUMMARY_TRUNCATE_LEN = 60
_FAILED_DAG_RUN_STATES = frozenset({"failed"})
_FAILED_TASK_STATES = frozenset({"failed", "upstream_failed", "up_for_retry"})


def _safe_id(value: str) -> str:
    return truncate(str(value).replace("\n", " "), _ID_SUMMARY_TRUNCATE_LEN)


def _map_get_recent_airflow_failures(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the count of failed/retrying task instances found for the DAG."""
    if output.get("error"):
        return
    failures = output.get("failures") or []
    if not failures:
        return
    summary = f"{len(failures)} failed/retrying task instance(s)"
    dag_id = output.get("dag_id")
    if dag_id:
        summary += f" for DAG '{_safe_id(dag_id)}'"
    record_evidence_entry(
        evidence,
        source="get_recent_airflow_failures",
        label="Airflow Recent Failures",
        summary=summary,
    )


def _map_get_airflow_dag_runs(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite the DAG run count and how many are in a failed state.

    ``state`` filters the API query itself (only runs in that state come
    back), so when it's set the "N failed" count would either be trivially
    all of them (state="failed") or a misleading, unqualified zero for any
    other state -- the caller filtered failures out of view, not confirmed
    their absence. Cite the filter instead of a derived failure count in
    that case.
    """
    if output.get("error"):
        return
    dag_runs = output.get("dag_runs") or []
    if not dag_runs:
        return
    total = len(dag_runs)
    requested_limit = tool_input.get("limit", 10)
    count_label = f"{total}+" if total >= max(requested_limit, 1) else str(total)
    state_filter = tool_input.get("state")
    if state_filter:
        summary = f"{count_label} DAG run(s) with state '{_safe_id(str(state_filter))}'"
    else:
        failed = sum(
            1 for run in dag_runs if str(run.get("state", "")).lower() in _FAILED_DAG_RUN_STATES
        )
        summary = f"{count_label} DAG run(s), {failed} failed"
    dag_id = output.get("dag_id")
    if dag_id:
        summary += f" for '{_safe_id(dag_id)}'"
    record_evidence_entry(
        evidence,
        source="get_airflow_dag_runs",
        label="Airflow DAG Runs",
        summary=summary,
    )


def _map_get_airflow_task_instances(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the task instance count and how many failed or are up for retry."""
    if output.get("error"):
        return
    task_instances = output.get("task_instances") or []
    if not task_instances:
        return
    failed = sum(
        1 for t in task_instances if str(t.get("state", "")).lower() in _FAILED_TASK_STATES
    )
    summary = f"{len(task_instances)} task instance(s), {failed} failed/retrying"
    dag_run_id = output.get("dag_run_id")
    if dag_run_id:
        summary += f" for run '{_safe_id(dag_run_id)}'"
    record_evidence_entry(
        evidence,
        source="get_airflow_task_instances",
        label="Airflow Task Instances",
        summary=summary,
    )


def _airflow_available(sources: dict[str, Any]) -> bool:
    return "airflow" in sources


def _airflow_source(sources: dict[str, Any]) -> dict[str, Any]:
    source = sources.get("airflow", {})
    return source if isinstance(source, dict) else {}


def _airflow_config(sources: dict[str, Any]) -> AirflowConfig:
    source = _airflow_source(sources)
    return build_airflow_config(source)


def _airflow_dag_id(sources: dict[str, Any]) -> str:
    source = _airflow_source(sources)
    return str(
        source.get("dag_id") or source.get("pipeline_name") or os.getenv("AIRFLOW_DAG_ID", "")
    ).strip()


@tool(
    name="get_recent_airflow_failures",
    source="airflow",
    description="Fetch recent failed or retrying Airflow task evidence for a DAG.",
    use_cases=[
        "Investigating Airflow DAG failures",
        "Finding failed or retrying task instances",
        "Grounding RCA in Airflow DAG/task evidence",
    ],
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.CHAT),
    requires=["dag_id"],
    input_schema={
        "type": "object",
        "properties": {
            "dag_id": {"type": "string"},
            "limit": {"type": "integer", "default": 5},
        },
        "required": ["dag_id"],
    },
    is_available=_airflow_available,
    extract_params=lambda sources: {
        "config": _airflow_config(sources),
        "dag_id": _airflow_dag_id(sources),
    },
    evidence_mapper=_map_get_recent_airflow_failures,
)
def get_recent_airflow_failures(
    config: AirflowConfig,
    dag_id: str,
    limit: int = 5,
) -> dict[str, Any]:
    """Fetch recent failed or retrying Airflow task evidence for a DAG."""
    dag_id = dag_id or os.getenv("AIRFLOW_DAG_ID", "")
    if not dag_id:
        return {"error": "dag_id is required"}

    return {
        "source": "airflow",
        "dag_id": dag_id,
        "failures": fetch_recent_airflow_failures(
            config=config,
            dag_id=dag_id,
            limit=limit,
        ),
    }


@tool(
    name="get_airflow_dag_runs",
    source="airflow",
    description="Fetch recent Airflow DAG runs for a DAG.",
    use_cases=[
        "Checking recent Airflow DAG run state",
        "Finding failed DAG runs",
        "Validating Airflow orchestration state",
    ],
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.CHAT),
    requires=["dag_id"],
    input_schema={
        "type": "object",
        "properties": {
            "dag_id": {"type": "string"},
            "limit": {"type": "integer", "default": 10},
            "state": {"type": "string"},
        },
        "required": ["dag_id"],
    },
    is_available=_airflow_available,
    extract_params=lambda sources: {
        "config": _airflow_config(sources),
        "dag_id": _airflow_dag_id(sources),
    },
    evidence_mapper=_map_get_airflow_dag_runs,
)
def get_airflow_dag_runs(
    config: AirflowConfig,
    dag_id: str,
    limit: int = 10,
    state: str | None = None,
) -> dict[str, Any]:
    """Fetch recent Airflow DAG runs for a DAG."""
    dag_id = dag_id or os.getenv("AIRFLOW_DAG_ID", "")
    if not dag_id:
        return {"error": "dag_id is required"}

    return {
        "source": "airflow",
        "dag_id": dag_id,
        "dag_runs": fetch_airflow_dag_runs(
            config=config,
            dag_id=dag_id,
            limit=limit,
            state=state,
        ),
    }


@tool(
    name="get_airflow_task_instances",
    source="airflow",
    description="Fetch Airflow task instances for a specific DAG run.",
    use_cases=[
        "Inspecting failed Airflow task instances",
        "Finding task-level failure evidence",
        "Grounding RCA in Airflow task state",
    ],
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.CHAT),
    requires=["dag_id", "dag_run_id"],
    input_schema={
        "type": "object",
        "properties": {
            "dag_id": {"type": "string"},
            "dag_run_id": {"type": "string"},
        },
        "required": ["dag_id", "dag_run_id"],
    },
    is_available=_airflow_available,
    extract_params=lambda sources: {
        "config": _airflow_config(sources),
        "dag_id": _airflow_dag_id(sources),
    },
    evidence_mapper=_map_get_airflow_task_instances,
)
def get_airflow_task_instances(
    config: AirflowConfig,
    dag_id: str,
    dag_run_id: str,
) -> dict[str, Any]:
    """Fetch Airflow task instances for a DAG run."""
    if not dag_id:
        return {"error": "dag_id is required"}
    if not dag_run_id:
        return {"error": "dag_run_id is required"}

    return {
        "source": "airflow",
        "dag_id": dag_id,
        "dag_run_id": dag_run_id,
        "task_instances": fetch_airflow_task_instances(
            config=config,
            dag_id=dag_id,
            dag_run_id=dag_run_id,
        ),
    }
