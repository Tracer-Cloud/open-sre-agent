from __future__ import annotations

import pytest

from cli.error_mapping import reraise_cli_runtime_error
from interactive_shell.utils.error_handling.errors import OpenSREError


def test_reraise_maps_datadog_auth_failure_to_verify_hint() -> None:
    with pytest.raises(OpenSREError) as exc_info:
        reraise_cli_runtime_error(RuntimeError("Datadog API returned 403 Forbidden"))

    assert "Datadog" in str(exc_info.value)
    assert "opensre integrations verify datadog" in (exc_info.value.suggestion or "")


def test_reraise_maps_missing_grafana_token_to_verify_hint() -> None:
    with pytest.raises(OpenSREError) as exc_info:
        reraise_cli_runtime_error(
            RuntimeError("GRAFANA_READ_TOKEN is not set; Grafana integration unavailable")
        )

    assert "Grafana" in str(exc_info.value)
    assert "opensre integrations verify grafana" in (exc_info.value.suggestion or "")


def test_reraise_maps_alertmanager_auth_failure_to_verify_hint() -> None:
    with pytest.raises(OpenSREError) as exc_info:
        reraise_cli_runtime_error(RuntimeError("Alertmanager request unauthorized (401)"))

    assert "Alertmanager" in str(exc_info.value)
    assert "opensre integrations verify alertmanager" in (exc_info.value.suggestion or "")


def test_reraise_does_not_attach_hint_for_unrelated_runtime_error() -> None:
    with pytest.raises(RuntimeError, match="failed to parse grafana dashboard json"):
        reraise_cli_runtime_error(RuntimeError("failed to parse grafana dashboard json"))
