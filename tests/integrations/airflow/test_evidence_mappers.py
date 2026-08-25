"""Evidence mapper tests for the Airflow investigation tools."""

from __future__ import annotations

from typing import Any

from integrations.tracer.tools.tracer_airflow_dag_tool import (
    _map_get_airflow_dag_runs,
    _map_get_airflow_task_instances,
    _map_get_recent_airflow_failures,
)
from tools.registry import get_registered_tool


def test_dag_runs_mapper_records_entry() -> None:
    evidence: dict[str, Any] = {}
    output = {
        "source": "airflow",
        "dag_id": "etl_daily",
        "dag_runs": [
            {"dag_run_id": "run_1", "state": "success"},
            {"dag_run_id": "run_2", "state": "failed"},
        ],
    }

    _map_get_airflow_dag_runs(evidence, output, {})

    entries = evidence.get("catalog_entries")
    assert isinstance(entries, list)
    assert entries == [
        {
            "source": "get_airflow_dag_runs",
            "label": "Airflow DAG Runs",
            "summary": "2 dag runs",
            "url": None,
            "snippet": None,
        }
    ]


def test_dag_runs_mapper_records_nothing_without_runs() -> None:
    evidence: dict[str, Any] = {}

    _map_get_airflow_dag_runs(
        evidence, {"source": "airflow", "dag_id": "etl_daily", "dag_runs": []}, {}
    )

    assert "catalog_entries" not in evidence


def test_dag_runs_mapper_records_nothing_on_error_payload() -> None:
    evidence: dict[str, Any] = {}

    _map_get_airflow_dag_runs(evidence, {"error": "dag_id is required"}, {})

    assert "catalog_entries" not in evidence


def test_task_instances_mapper_records_entry() -> None:
    evidence: dict[str, Any] = {}
    output = {
        "source": "airflow",
        "dag_id": "etl_daily",
        "dag_run_id": "run_1",
        "task_instances": [
            {"task_id": "extract", "state": "success"},
            {"task_id": "load", "state": "failed"},
        ],
    }

    _map_get_airflow_task_instances(evidence, output, {})

    entries = evidence.get("catalog_entries")
    assert isinstance(entries, list)
    assert entries == [
        {
            "source": "get_airflow_task_instances",
            "label": "Airflow Task Instances",
            "summary": "2 task instances",
            "url": None,
            "snippet": None,
        }
    ]


def test_task_instances_mapper_records_nothing_without_instances() -> None:
    evidence: dict[str, Any] = {}

    _map_get_airflow_task_instances(
        evidence,
        {"source": "airflow", "dag_id": "etl_daily", "dag_run_id": "run_1", "task_instances": []},
        {},
    )

    assert "catalog_entries" not in evidence


def test_task_instances_mapper_records_nothing_on_error_payload() -> None:
    evidence: dict[str, Any] = {}

    _map_get_airflow_task_instances(evidence, {"error": "dag_run_id is required"}, {})

    assert "catalog_entries" not in evidence


def test_recent_failures_mapper_records_entry() -> None:
    evidence: dict[str, Any] = {}
    output = {
        "source": "airflow",
        "dag_id": "etl_daily",
        "failures": [
            {"dag_run_id": "run_1", "task_id": "load", "task_state": "failed"},
        ],
    }

    _map_get_recent_airflow_failures(evidence, output, {})

    entries = evidence.get("catalog_entries")
    assert isinstance(entries, list)
    assert entries == [
        {
            "source": "get_recent_airflow_failures",
            "label": "Airflow Recent Failures",
            "summary": "1 failures",
            "url": None,
            "snippet": None,
        }
    ]


def test_recent_failures_mapper_records_nothing_without_failures() -> None:
    evidence: dict[str, Any] = {}

    _map_get_recent_airflow_failures(
        evidence, {"source": "airflow", "dag_id": "etl_daily", "failures": []}, {}
    )

    assert "catalog_entries" not in evidence


def test_recent_failures_mapper_records_nothing_on_error_payload() -> None:
    evidence: dict[str, Any] = {}

    _map_get_recent_airflow_failures(evidence, {"error": "dag_id is required"}, {})

    assert "catalog_entries" not in evidence


def test_registered_tools_carry_their_mappers() -> None:
    dag_runs_tool = get_registered_tool("get_airflow_dag_runs")
    task_instances_tool = get_registered_tool("get_airflow_task_instances")
    recent_failures_tool = get_registered_tool("get_recent_airflow_failures")

    assert dag_runs_tool is not None
    assert task_instances_tool is not None
    assert recent_failures_tool is not None
    assert dag_runs_tool.evidence_mapper is _map_get_airflow_dag_runs
    assert task_instances_tool.evidence_mapper is _map_get_airflow_task_instances
    assert recent_failures_tool.evidence_mapper is _map_get_recent_airflow_failures
