"""Tests for #1459: stop silent ``make_*_client`` factory failures.

Seven service-client factories previously collapsed both "not configured"
(required field absent) and "broken" (constructor blew up — validator
refused, malformed URL, bad type) into the same ``None`` return:

    client = make_argocd_client(...)
    if client is None:
        return  # treat as "not configured"

After this change, the "not configured" branch still returns ``None``
silently (intentional absence is correct), but the *broken* branch routes
the exception through ``report_factory_failure`` →
``app.utils.errors.report_exception`` before returning ``None``. The
caller contract is preserved end-to-end — the only behaviour delta is
that the swallowed exception is no longer invisible.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from app.services import _factory_telemetry
from app.services.alertmanager.client import make_alertmanager_client
from app.services.argocd.client import make_argocd_client
from app.services.jira.client import make_jira_client
from app.services.opsgenie.client import make_opsgenie_client
from app.services.prefect.client import make_prefect_client
from app.services.vercel.client import make_vercel_client
from app.services.victoria_logs.client import make_victoria_logs_client


@pytest.fixture(autouse=True)
def _quiet_sentry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_NO_TELEMETRY", "1")


# ---------------------------------------------------------------------------
# Helper smoke test
# ---------------------------------------------------------------------------


def test_report_factory_failure_forwards_tags() -> None:
    import logging

    exc = ValueError("validator rejected URL")
    with patch("app.services._factory_telemetry.report_exception") as mock_report:
        _factory_telemetry.report_factory_failure(
            exc, integration="argocd", logger=logging.getLogger("test")
        )
    mock_report.assert_called_once()
    kwargs = mock_report.call_args.kwargs
    assert kwargs["severity"] == "warning"
    assert kwargs["tags"] == {
        "surface": "service_client",
        "component": "app.services.argocd.client",
        "integration": "argocd",
        "event": "factory_failure",
    }


# ---------------------------------------------------------------------------
# Per-factory parametrized cases
# ---------------------------------------------------------------------------


# (factory, integration_name, args-that-pass-precheck, client-symbol-to-break)
#
# Each row:
#  * factory      — the make_*_client callable under test
#  * integration  — vendor tag expected in the Sentry call
#  * configured_kwargs — kwargs that satisfy the factory's pre-check so we
#                       reach the inner try/except construction site
#  * client_symbol — the dotted path of the Client class to monkeypatch so
#                    its __init__ raises (proves the surrounding try/except
#                    routes the failure)
_FACTORY_CASES: list[tuple[Any, str, dict[str, Any], str]] = [
    (
        make_argocd_client,
        "argocd",
        {"base_url": "https://argo.example", "bearer_token": "t"},
        "app.services.argocd.client.ArgoCDClient",
    ),
    (
        make_jira_client,
        "jira",
        {
            "base_url": "https://jira.example",
            "email": "user@example.com",
            "api_token": "t",
        },
        "app.services.jira.client.JiraClient",
    ),
    (
        make_alertmanager_client,
        "alertmanager",
        {"base_url": "https://am.example"},
        "app.services.alertmanager.client.AlertmanagerClient",
    ),
    (
        make_opsgenie_client,
        "opsgenie",
        {"api_key": "k"},
        "app.services.opsgenie.client.OpsGenieClient",
    ),
    (
        make_prefect_client,
        "prefect",
        {"api_url": "https://prefect.example"},
        "app.services.prefect.client.PrefectClient",
    ),
    (
        make_vercel_client,
        "vercel",
        {"api_token": "t"},
        "app.services.vercel.client.VercelClient",
    ),
    (
        make_victoria_logs_client,
        "victoria_logs",
        {"base_url": "https://vl.example"},
        "app.services.victoria_logs.client.VictoriaLogsClient",
    ),
]


@pytest.mark.parametrize(
    ("factory", "integration", "configured_kwargs", "client_symbol"),
    _FACTORY_CASES,
)
def test_factory_construction_failure_reports_and_returns_none(
    factory: Any,
    integration: str,
    configured_kwargs: dict[str, Any],
    client_symbol: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the configured factory hits an exception inside ``__init__``,
    it must:

      * still return ``None`` (preserves caller contract: callers do
        ``if client is None: return  # treat as unavailable``);
      * route the exception through ``report_factory_failure`` with the
        right vendor tag and ``event=factory_failure``;
      * use ``severity=warning`` (these are user-config misses, not
        service bugs)."""

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError(f"forced {integration} construction failure")

    monkeypatch.setattr(client_symbol, _boom)

    module = client_symbol.rsplit(".", 1)[0]
    with patch(f"{module}.report_factory_failure") as mock_report:
        result = factory(**configured_kwargs)

    assert result is None, (
        f"caller contract broken for {integration}: factory must still return None "
        "on construction failure"
    )
    assert mock_report.call_count == 1, f"{integration} factory silently swallowed the exception"
    kwargs = mock_report.call_args.kwargs
    assert kwargs["integration"] == integration


# ---------------------------------------------------------------------------
# Negative case: "not configured" path stays silent
# ---------------------------------------------------------------------------


_NOT_CONFIGURED_CASES: list[tuple[Any, str, dict[str, Any]]] = [
    (make_argocd_client, "argocd", {"base_url": None}),
    (make_argocd_client, "argocd", {"base_url": "https://argo.example"}),  # no auth
    (make_jira_client, "jira", {"base_url": None, "email": "x", "api_token": "y"}),
    (make_alertmanager_client, "alertmanager", {"base_url": ""}),
    (make_opsgenie_client, "opsgenie", {"api_key": ""}),
    (make_prefect_client, "prefect", {"api_url": ""}),
    (make_vercel_client, "vercel", {"api_token": ""}),
    (make_victoria_logs_client, "victoria_logs", {"base_url": ""}),
]


@pytest.mark.parametrize(
    ("factory", "integration", "kwargs"),
    _NOT_CONFIGURED_CASES,
)
def test_factory_not_configured_path_stays_silent(
    factory: Any,
    integration: str,
    kwargs: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-check "required field absent → return None" branch must NOT
    fire ``report_factory_failure`` — those are intentional empty configs.

    Inverting this would flood Sentry on every dev box that hasn't
    configured a given integration (i.e. essentially every box)."""
    # Pin the helper across all client modules to a counter; assert untouched.
    calls: list[Any] = []
    for client_symbol in {row[3].rsplit(".", 1)[0] for row in _FACTORY_CASES}:
        monkeypatch.setattr(
            f"{client_symbol}.report_factory_failure",
            lambda *_a, **_k: calls.append(("UNEXPECTED",)),
        )

    result = factory(**kwargs)

    assert result is None, f"{integration}: not-configured path should yield None"
    assert calls == [], (
        f"{integration}: not-configured path must NOT report — empty config is intentional"
    )
