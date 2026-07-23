"""Regression tests for deterministic Grafana investigation seed calls."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from integrations.config_models import GrafanaIntegrationConfig
from integrations.grafana import tools as grafana_tools
from integrations.grafana.tools import _GRAFANA_RUNTIME_PARAMS
from tools.investigation.stages.gather_evidence.tools import build_seed_calls

GRAFANA_TOOL_FUNCTIONS: tuple[Callable[..., dict[str, Any]], ...] = (
    grafana_tools.query_grafana_alert_rules,
    grafana_tools.query_grafana_annotations,
    grafana_tools.query_grafana_logs,
    grafana_tools.query_grafana_metrics,
    grafana_tools.query_grafana_service_names,
    grafana_tools.query_grafana_traces,
)

GRAFANA_RUNTIME_PARAMS = set(_GRAFANA_RUNTIME_PARAMS)


def _tool_id(tool_function: Callable[..., dict[str, Any]]) -> str:
    return tool_function.__name__


def _local_basic_auth_state() -> dict[str, Any]:
    return {
        "alert_source": "grafana",
        "resolved_integrations": {
            "grafana_local": GrafanaIntegrationConfig(
                endpoint="http://127.0.0.1:3001",
                username="admin",
                password="secret",
            )
        },
    }


@pytest.mark.parametrize("tool_function", GRAFANA_TOOL_FUNCTIONS, ids=_tool_id)
def test_grafana_seed_call_has_valid_public_input(
    tool_function: Callable[..., dict[str, Any]],
) -> None:
    """Every deterministic seed must pass the same schema validation as an LLM call."""
    registered = tool_function.__opensre_registered_tool__  # type: ignore[attr-defined]

    calls = build_seed_calls(_local_basic_auth_state(), [registered], object())

    assert len(calls) == 1
    assert registered.validate_public_input(calls[0].input) is None


@pytest.mark.parametrize("tool_function", GRAFANA_TOOL_FUNCTIONS, ids=_tool_id)
def test_grafana_connection_params_are_runtime_injected(
    tool_function: Callable[..., dict[str, Any]],
) -> None:
    """Grafana connection details come from config and must never be model input."""
    registered = tool_function.__opensre_registered_tool__  # type: ignore[attr-defined]

    assert set(registered.injected_params) >= GRAFANA_RUNTIME_PARAMS
