"""GCP tools: filters, payload normalization, aligner choice, and fan-out."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from config.constants.gcp import GCP_AUTO_REGISTER_GKE_ENV
from integrations.gcp.tools.gcp_list_projects_tool import gcp_list_projects
from integrations.gcp.tools.gcp_logging_query_tool import gcp_logging_query
from integrations.gcp.tools.gcp_logging_query_tool.entries import extract_message, normalize_entry
from integrations.gcp.tools.gcp_logging_query_tool.filters import build_filter, normalize_severity
from integrations.gcp.tools.gcp_monitoring_query_tool import gcp_monitoring_query
from integrations.gcp.tools.gcp_monitoring_query_tool.aligners import (
    aligner_for_kind,
    extract_metric_type,
)
from integrations.gcp.tools.gcp_monitoring_query_tool.series import normalize_series, point_value
from integrations.gcp.tools.gcp_refresh_discovery_tool import gcp_refresh_discovery
from tools.registry import get_registered_tool

_NOW = datetime(2026, 8, 5, 12, 0, 0, tzinfo=UTC)

# --- registry ----------------------------------------------------------------


@pytest.mark.parametrize("name", ["gcp_logging_query", "gcp_monitoring_query", "gcp_list_projects"])
def test_tool_is_registered(name: str) -> None:
    registered = get_registered_tool(name)

    assert registered is not None
    assert registered.source == "gcp"


def test_project_is_not_an_injected_param() -> None:
    registered = get_registered_tool("gcp_logging_query")

    assert registered is not None
    # Injected params override model input, which would make `project` inert.
    assert "project" not in (registered.injected_params or ())


# --- log filters -------------------------------------------------------------


def test_build_filter_always_bounds_the_window() -> None:
    built = build_filter(user_filter="", severity="", hours=2, now=_NOW)

    assert built == 'timestamp >= "2026-08-05T10:00:00Z"'


def test_build_filter_parenthesises_the_caller_filter() -> None:
    built = build_filter(user_filter='a="1" OR b="2"', severity="", hours=1, now=_NOW)

    # Without the parentheses the OR would escape the timestamp bound and the
    # query would silently read the whole retention window.
    assert built == 'timestamp >= "2026-08-05T11:00:00Z" AND (a="1" OR b="2")'


def test_build_filter_adds_a_severity_floor() -> None:
    built = build_filter(user_filter="", severity="error", hours=1, now=_NOW)

    assert "severity >= ERROR" in built


def test_build_filter_ignores_an_unknown_severity() -> None:
    built = build_filter(user_filter="", severity="loud", hours=1, now=_NOW)

    assert "severity" not in built


def test_normalize_severity_widens_rather_than_failing() -> None:
    assert normalize_severity("warning") == "WARNING"
    assert normalize_severity("nonsense") is None


def test_build_filter_caps_the_lookback() -> None:
    far = build_filter(user_filter="", severity="", hours=24 * 365, now=_NOW)

    # 30 days back from _NOW, not a year.
    assert '"2026-07-06T12:00:00Z"' in far


# --- log entries -------------------------------------------------------------


def test_extract_message_reads_text_payload() -> None:
    assert extract_message({"textPayload": " boom "}) == "boom"


def test_extract_message_reads_a_structured_payload() -> None:
    assert extract_message({"jsonPayload": {"message": "structured boom"}}) == "structured boom"


def test_extract_message_serializes_an_audit_payload() -> None:
    # Audit entries have no conventional message key; returning "" would hide
    # exactly the entries an investigation most needs.
    message = extract_message({"protoPayload": {"methodName": "SetIamPolicy"}})

    assert json.loads(message) == {"methodName": "SetIamPolicy"}


def test_normalize_entry_flattens_gke_labels() -> None:
    normalized = normalize_entry(
        {
            "timestamp": "2026-08-05T12:00:00Z",
            "severity": "ERROR",
            "textPayload": "crash",
            "logName": "projects/acme/logs/stdout",
            "trace": "projects/acme/traces/abc123",
            "resource": {
                "type": "k8s_container",
                "labels": {
                    "project_id": "acme",
                    "cluster_name": "prod",
                    "namespace_name": "payments",
                    "pod_name": "api-1",
                    "container_name": "api",
                },
            },
        }
    )

    assert normalized["log_name"] == "stdout"
    assert normalized["trace"] == "abc123"
    assert normalized["pod_name"] == "api-1"
    assert normalized["namespace_name"] == "payments"


# --- time series -------------------------------------------------------------


def test_point_value_decodes_int64_from_a_json_string() -> None:
    assert point_value({"int64Value": "42"}) == 42


def test_point_value_summarises_a_distribution() -> None:
    assert point_value({"distributionValue": {"count": "3", "mean": 1.5}}) == {
        "count": "3",
        "mean": 1.5,
    }


def test_normalize_series_returns_points_in_causal_order() -> None:
    normalized = normalize_series(
        {
            "metric": {"type": "kubernetes.io/container/memory/used_bytes"},
            "resource": {"type": "k8s_container"},
            "points": [
                {"interval": {"endTime": "t2"}, "value": {"doubleValue": 2.0}},
                {"interval": {"endTime": "t1"}, "value": {"doubleValue": 1.0}},
            ],
        }
    )

    assert [point["time"] for point in normalized["points"]] == ["t1", "t2"]


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("GAUGE", "ALIGN_MEAN"),
        ("CUMULATIVE", "ALIGN_RATE"),
        ("DELTA", "ALIGN_RATE"),
        ("", "ALIGN_MEAN"),
    ],
)
def test_aligner_matches_the_metric_kind(kind: str, expected: str) -> None:
    assert aligner_for_kind(kind) == expected


def test_extract_metric_type_reads_the_filter() -> None:
    assert extract_metric_type('resource.type="k8s_container" AND metric.type="a/b/c"') == "a/b/c"


# --- fakes -------------------------------------------------------------------


class _FakeEntries:
    def __init__(self, calls: list[dict[str, Any]], pages: list[dict[str, Any]]) -> None:
        self._calls = calls
        self._pages = pages

    def list(self, body: dict[str, Any]) -> _FakeEntries:
        self._calls.append(body)
        return self

    def execute(self) -> dict[str, Any]:
        return self._pages.pop(0)


class _FakeLoggingService:
    def __init__(self, calls: list[dict[str, Any]], pages: list[dict[str, Any]]) -> None:
        self._entries = _FakeEntries(calls, pages)

    def entries(self) -> _FakeEntries:
        return self._entries


def _entry(timestamp: str, message: str) -> dict[str, Any]:
    return {"timestamp": timestamp, "severity": "ERROR", "textPayload": message}


# --- logging tool ------------------------------------------------------------


def test_logging_query_rejects_an_unknown_project() -> None:
    result = gcp_logging_query(project="nope", default_project="acme", available_projects=["acme"])

    assert result["available"] is False
    assert "nope" in result["error"]


def test_logging_query_batches_one_credential_into_one_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import integrations.gcp.tools.gcp_logging_query_tool as module

    calls: list[dict[str, Any]] = []
    pages = [{"entries": [_entry("2026-08-05T12:00:00Z", "boom")]}]
    monkeypatch.setattr(
        module, "build_service", lambda _config, _api: _FakeLoggingService(calls, pages)
    )

    config = {"project_id": "acme", "additional_projects": ["acme-staging"]}
    result = gcp_logging_query(
        project="*",
        default_project="acme",
        available_projects=["acme", "acme-staging"],
        project_configs={"acme": config, "acme-staging": config},
    )

    assert len(calls) == 1
    assert calls[0]["resourceNames"] == ["projects/acme", "projects/acme-staging"]
    assert result["found"] is True
    assert result["projects"] == ["acme", "acme-staging"]


def test_logging_query_splits_a_discovered_estate_across_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cloud Logging takes at most 100 resourceNames and rejects the whole call above it.

    Reachable in one line via ``GCP_ADDITIONAL_PROJECTS=discover``: an org-level
    grant resolves ``project="*"`` to the entire estate, so without splitting,
    the broadest query is the one guaranteed to fail. One credential, so still
    one client — but three requests, merged newest-first like any other fan-out.
    """
    import integrations.gcp.tools.gcp_logging_query_tool as module

    projects = [f"p{index:03d}" for index in range(250)]
    config = {"project_id": projects[0]}
    calls: list[dict[str, Any]] = []
    pages = [
        {"entries": [_entry("2026-08-05T10:00:00Z", "oldest")]},
        {"entries": [_entry("2026-08-05T12:00:00Z", "newest")]},
        {"entries": [_entry("2026-08-05T11:00:00Z", "middle")]},
    ]
    monkeypatch.setattr(
        module, "build_service", lambda _config, _api: _FakeLoggingService(calls, pages)
    )

    result = gcp_logging_query(
        project="*",
        default_project=projects[0],
        available_projects=projects,
        project_configs=dict.fromkeys(projects, config),
    )

    assert [len(call["resourceNames"]) for call in calls] == [100, 100, 50]
    # Split, not sampled: every project is queried exactly once.
    queried = [name for call in calls for name in call["resourceNames"]]
    assert queried == [f"projects/{project}" for project in projects]
    assert [entry["message"] for entry in result["entries"]] == ["newest", "middle", "oldest"]


def test_logging_query_fans_out_across_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    import integrations.gcp.tools.gcp_logging_query_tool as module

    calls: list[dict[str, Any]] = []
    pages = [
        {"entries": [_entry("2026-08-05T11:00:00Z", "older")]},
        {"entries": [_entry("2026-08-05T12:00:00Z", "newer")]},
    ]
    monkeypatch.setattr(
        module, "build_service", lambda _config, _api: _FakeLoggingService(calls, pages)
    )

    result = gcp_logging_query(
        project="*",
        default_project="acme",
        available_projects=["acme", "research"],
        project_configs={
            "acme": {"project_id": "acme"},
            "research": {"project_id": "research", "impersonate_service_account": "ro@x"},
        },
    )

    assert [call["resourceNames"] for call in calls] == [
        ["projects/acme"],
        ["projects/research"],
    ]
    # Two newest-first pages merged back into one newest-first list.
    assert [entry["message"] for entry in result["entries"]] == ["newer", "older"]


def test_logging_query_marks_a_merged_result_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import integrations.gcp.tools.gcp_logging_query_tool as module

    calls: list[dict[str, Any]] = []
    pages = [
        {"entries": [_entry("2026-08-05T11:00:00Z", "a")]},
        {"entries": [_entry("2026-08-05T12:00:00Z", "b")]},
    ]
    monkeypatch.setattr(
        module, "build_service", lambda _config, _api: _FakeLoggingService(calls, pages)
    )

    result = gcp_logging_query(
        project="*",
        limit=1,
        default_project="acme",
        available_projects=["acme", "research"],
        project_configs={
            "acme": {"project_id": "acme"},
            "research": {"project_id": "research", "impersonate_service_account": "ro@x"},
        },
    )

    assert result["entry_count"] == 1
    assert result["truncated"] is True


# --- monitoring tool ---------------------------------------------------------


class _FakeTimeSeries:
    def __init__(self, aligners: list[str], responses: list[Any]) -> None:
        self._aligners = aligners
        self._responses = responses

    def list(self, **kwargs: Any) -> _FakeTimeSeries:
        self._aligners.append(str(kwargs["aggregation_perSeriesAligner"]))
        return self

    def execute(self) -> dict[str, Any]:
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        assert isinstance(response, dict)
        return response


class _FakeMetricDescriptors:
    def __init__(self, kind: str) -> None:
        self._kind = kind

    def get(self, name: str) -> _FakeMetricDescriptors:
        self._name = name
        return self

    def execute(self) -> dict[str, str]:
        if not self._kind:
            raise RuntimeError("descriptor unavailable")
        return {"metricKind": self._kind}


class _FakeMonitoringProjects:
    def __init__(self, aligners: list[str], responses: list[Any], kind: str) -> None:
        self._series = _FakeTimeSeries(aligners, responses)
        self._descriptors = _FakeMetricDescriptors(kind)

    def timeSeries(self) -> _FakeTimeSeries:  # noqa: N802 — Google API method name
        return self._series

    def metricDescriptors(self) -> _FakeMetricDescriptors:  # noqa: N802 — Google API method
        return self._descriptors


class _FakeMonitoringService:
    def __init__(self, aligners: list[str], responses: list[Any], kind: str) -> None:
        self._projects = _FakeMonitoringProjects(aligners, responses, kind)

    def projects(self) -> _FakeMonitoringProjects:
        return self._projects


class _AlignerRejected(Exception):
    status_code = 400
    content = json.dumps(
        {"error": {"message": "Field aggregation.perSeriesAligner had an invalid value"}}
    ).encode()


def _install_monitoring(
    monkeypatch: pytest.MonkeyPatch, responses: list[Any], kind: str
) -> list[str]:
    import integrations.gcp.tools.gcp_monitoring_query_tool as module

    aligners: list[str] = []
    monkeypatch.setattr(
        module,
        "build_service",
        lambda _config, _api: _FakeMonitoringService(aligners, responses, kind),
    )
    return aligners


def test_monitoring_query_requires_a_filter() -> None:
    result = gcp_monitoring_query(default_project="acme", available_projects=["acme"])

    assert result["available"] is False
    assert "filter is required" in result["error"]


def test_monitoring_query_rejects_multiple_projects() -> None:
    result = gcp_monitoring_query(
        filter='metric.type="a/b"',
        project="*",
        default_project="acme",
        available_projects=["acme", "other"],
    )

    assert result["available"] is False
    assert "one project per call" in result["error"]


def test_monitoring_query_rates_a_cumulative_metric(monkeypatch: pytest.MonkeyPatch) -> None:
    aligners = _install_monitoring(monkeypatch, [{"timeSeries": []}], "CUMULATIVE")

    result = gcp_monitoring_query(
        filter='metric.type="a/b/c"', default_project="acme", available_projects=["acme"]
    )

    assert aligners == ["ALIGN_RATE"]
    assert result["aligner"] == "ALIGN_RATE"


def test_monitoring_query_honours_an_explicit_aligner(monkeypatch: pytest.MonkeyPatch) -> None:
    aligners = _install_monitoring(monkeypatch, [{"timeSeries": []}], "CUMULATIVE")

    gcp_monitoring_query(
        filter='metric.type="a/b/c"',
        aligner="align_max",
        default_project="acme",
        available_projects=["acme"],
    )

    assert aligners == ["ALIGN_MAX"]


def test_monitoring_query_retries_a_rejected_aligner(monkeypatch: pytest.MonkeyPatch) -> None:
    # No descriptor available, so the first guess is the gauge default and the
    # API rejects it — the retry is what keeps counters usable.
    aligners = _install_monitoring(monkeypatch, [_AlignerRejected(), {"timeSeries": []}], kind="")

    result = gcp_monitoring_query(
        filter='metric.type="a/b/c"', default_project="acme", available_projects=["acme"]
    )

    assert aligners == ["ALIGN_MEAN", "ALIGN_RATE"]
    assert result["aligner"] == "ALIGN_RATE"


def test_monitoring_query_does_not_retry_an_explicit_aligner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aligners = _install_monitoring(monkeypatch, [_AlignerRejected()], kind="")

    result = gcp_monitoring_query(
        filter='metric.type="a/b/c"',
        aligner="ALIGN_MEAN",
        default_project="acme",
        available_projects=["acme"],
    )

    assert aligners == ["ALIGN_MEAN"]
    assert result["found"] is False


# --- list projects tool ------------------------------------------------------


class _FakeRMProjects:
    def __init__(self, response: Any) -> None:
        self._response = response

    def list(self, pageSize: int) -> _FakeRMProjects:  # noqa: N803 — Google API kwarg
        self._page_size = pageSize
        return self

    def execute(self) -> dict[str, Any]:
        if isinstance(self._response, Exception):
            raise self._response
        assert isinstance(self._response, dict)
        return self._response


class _FakeRMService:
    def __init__(self, response: Any) -> None:
        self._projects = _FakeRMProjects(response)

    def projects(self) -> _FakeRMProjects:
        return self._projects


def test_list_projects_is_unavailable_without_configuration() -> None:
    result = gcp_list_projects()

    assert result["available"] is False


def test_list_projects_merges_discovery_and_skips_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import integrations.gcp.project_discovery as module

    response = {
        "projects": [
            {"projectId": "acme", "lifecycleState": "ACTIVE"},
            {"projectId": "discovered", "lifecycleState": "ACTIVE"},
            {"projectId": "gone", "lifecycleState": "DELETE_REQUESTED"},
        ]
    }
    monkeypatch.setattr(module, "build_service", lambda _config, _api: _FakeRMService(response))

    result = gcp_list_projects(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": {"project_id": "acme"}},
    )

    assert result["projects"] == ["acme", "discovered"]
    assert "gone" not in result["discovered_projects"]
    # Discovered-but-unconfigured projects are visible, not queryable — say so.
    assert "note" in result


def test_list_projects_survives_a_missing_list_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import integrations.gcp.project_discovery as module

    monkeypatch.setattr(
        module,
        "build_service",
        lambda _config, _api: _FakeRMService(RuntimeError("permission denied")),
    )

    result = gcp_list_projects(
        default_project="acme",
        available_projects=["acme", "acme-staging"],
        project_configs={"acme": {"project_id": "acme"}},
    )

    # Losing the optional expansion must not lose the configured answer.
    assert result["projects"] == ["acme", "acme-staging"]
    assert "discovery_error" in result


# --- gcp_refresh_discovery -----------------------------------------------------


def _rm_returning(*project_ids: str) -> Any:
    """A Resource Manager stub that lists ``project_ids`` as ACTIVE."""
    response = {"projects": [{"projectId": pid, "lifecycleState": "ACTIVE"} for pid in project_ids]}
    return lambda _config, _api: _FakeRMService(response)


def test_refresh_re_reads_a_listing_the_cache_would_have_served(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reason the tool exists: a project created after the last listing.

    ``gcp_list_projects`` is answered from cache by design, so without a forced
    re-read the operator's only route to a newly created project is a restart.
    """
    import integrations.gcp.project_discovery as module

    monkeypatch.setenv(GCP_AUTO_REGISTER_GKE_ENV, "")
    monkeypatch.setattr(module, "build_service", _rm_returning("acme"))
    args: dict[str, Any] = {
        "default_project": "acme",
        "available_projects": ["acme"],
        "project_configs": {"acme": {"project_id": "acme"}},
    }

    cached = gcp_list_projects(**args)
    monkeypatch.setattr(module, "build_service", _rm_returning("acme", "created-just-now"))
    still_cached = gcp_list_projects(**args)
    refreshed = gcp_refresh_discovery(**args)

    assert cached["discovered_projects"] == ["acme"]
    assert still_cached["discovered_projects"] == ["acme"]
    assert refreshed["discovered_projects"] == ["acme", "created-just-now"]


def test_refresh_leaves_the_allow_list_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """It changes timing, not authority.

    A discovered project is queryable only if configuration allows it. If a
    forced refresh widened the allow-list, the tool would be a way for the model
    to grant itself read access to an estate the operator never configured.
    """
    import integrations.gcp.project_discovery as module

    monkeypatch.setenv(GCP_AUTO_REGISTER_GKE_ENV, "")
    monkeypatch.setattr(module, "build_service", _rm_returning("acme", "not-configured"))

    result = gcp_refresh_discovery(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": {"project_id": "acme"}},
    )

    assert result["configured_projects"] == ["acme"]
    assert "not-configured" in result["discovered_projects"]
    assert "note" in result


def test_refresh_reports_that_gke_registration_is_switched_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Half the tool does nothing unless opted in; silence would read as success."""
    import integrations.gcp.project_discovery as module

    monkeypatch.delenv(GCP_AUTO_REGISTER_GKE_ENV, raising=False)
    monkeypatch.setattr(module, "build_service", _rm_returning("acme"))

    result = gcp_refresh_discovery(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": {"project_id": "acme"}},
    )

    assert result["gke"]["enabled"] is False
    assert GCP_AUTO_REGISTER_GKE_ENV in result["gke"]["detail"]


def test_refresh_runs_gke_registration_when_it_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    import integrations.gcp.project_discovery as module
    from integrations.gcp.gke import autoregister

    summary = autoregister.RegistrationSummary(registered=1, instances=("gke-one",))

    def _register(_logger: Any, _scope: Any) -> autoregister.RegistrationSummary:
        return summary

    monkeypatch.setenv(GCP_AUTO_REGISTER_GKE_ENV, "true")
    monkeypatch.setattr(module, "build_service", _rm_returning("acme"))
    monkeypatch.setattr("integrations.gcp.tools.gcp_refresh_discovery_tool.register_now", _register)

    result = gcp_refresh_discovery(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": {"project_id": "acme"}},
    )

    assert result["gke"] == {
        "enabled": True,
        "ran": True,
        "registered": 1,
        "skipped": 0,
        "failed": 0,
        "new_instances": ["gke-one"],
    }


def test_a_failed_gke_pass_does_not_lose_the_project_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two independent halves; one failing must not discard the other's answer."""
    import integrations.gcp.project_discovery as module

    def _boom(_logger: Any, _scope: Any) -> None:
        raise RuntimeError("control plane unreachable")

    monkeypatch.setenv(GCP_AUTO_REGISTER_GKE_ENV, "true")
    monkeypatch.setattr(module, "build_service", _rm_returning("acme", "found"))
    monkeypatch.setattr("integrations.gcp.tools.gcp_refresh_discovery_tool.register_now", _boom)

    result = gcp_refresh_discovery(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": {"project_id": "acme"}},
    )

    assert result["discovered_projects"] == ["acme", "found"]
    assert result["gke"]["ran"] is False
    # The type name, and nothing else: a tool result reaches Slack.
    assert "RuntimeError" in result["gke"]["error"]
    assert "control plane unreachable" not in result["gke"]["error"]


def test_refresh_is_unavailable_without_configuration() -> None:
    result = gcp_refresh_discovery()

    assert result["available"] is False


# --- project selection guidance ----------------------------------------------

#: Every GCP tool whose ``project`` argument is the shared ``PROJECT_PROPERTY``.
#: ``gcp_monitoring_query`` is deliberately absent — one metrics scope per call,
#: so it carries its own text and is pinned separately below.
_TOOLS_WITH_SHARED_PROJECT_PROPERTY = (
    "gcp_audit_log_query",
    "gcp_error_reporting_top_errors",
    "gcp_list_cloud_run_services",
    "gcp_list_cloud_sql_instances",
    "gcp_list_compute_instances",
    "gcp_list_gke_clusters",
    "gcp_logging_query",
    "gcp_pubsub_backlog",
)


@pytest.mark.parametrize("name", _TOOLS_WITH_SHARED_PROJECT_PROPERTY)
def test_the_project_description_warns_that_omitting_narrows_to_the_default(name: str) -> None:
    """Omission is the silent failure, so the schema has to name it.

    The model's default behaviour is to leave an optional argument out. On a
    12-project estate that reads one project and reports a confident nothing,
    which is indistinguishable from a correct negative. The description is the
    only place that can say so before the call is made.
    """
    registered = get_registered_tool(name)
    assert registered is not None

    description = registered.input_schema["properties"]["project"]["description"]

    assert "Omitting" in description
    assert "default project" in description
    assert "'*'" in description
    assert "gcp_list_projects" in description


@pytest.mark.parametrize("name", _TOOLS_WITH_SHARED_PROJECT_PROPERTY)
def test_project_is_never_an_injected_param(name: str) -> None:
    """An injected key is re-forced over model input, which makes the arg inert."""
    registered = get_registered_tool(name)
    assert registered is not None

    assert "project" not in (registered.injected_params or ())


def test_monitoring_says_scope_not_narrower_reach() -> None:
    """One scope per call is a *call-shape* limit, not a reach limit.

    A scoping project's series already span every project it monitors. Text
    that reads like "monitoring only sees one project" would push the model
    into per-project fan-out it cannot do here, or into concluding a metric is
    missing when it is simply attributed to another project id. And ``'*'``
    must not appear: Cloud Monitoring rejects it outright.
    """
    registered = get_registered_tool("gcp_monitoring_query")
    assert registered is not None

    description = registered.input_schema["properties"]["project"]["description"]

    # The requirement is "must not *advertise* '*'". The mandated wording says
    # the tool takes "no list and no '*'", so the literal token is present as a
    # prohibition. Assert the prohibition rather than the token's absence:
    # Cloud Monitoring rejects '*' outright, and saying so beats silence.
    assert "no '*'" in description
    assert "metrics scope" in description
    assert "every project it monitors" in description
    # The recovery step, not just the constraint: the caller has to be told how
    # to tell the series apart once they arrive under one scope.
    assert "resource_labels.project_id" in description

    top_level = registered.description
    assert "one metrics scope" in top_level
    assert "one project" not in top_level

    anti = " ".join(registered.anti_examples or ())
    assert "call-shape limit, not a reach limit" in anti


def test_logging_query_sweeps_twelve_projects_in_one_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cloud Logging batches up to 100 resourceNames, so '*' is one call.

    This is what makes the "sweep with project='*'" guidance in the Kubernetes
    prompt fragment affordable. Fanning out per project would be twelve round
    trips and a much slower turn, and would tempt a future change to cap the
    sweep.
    """
    import integrations.gcp.tools.gcp_logging_query_tool as module

    projects = [f"proj-{index:02d}" for index in range(12)]
    config = {"project_id": projects[0], "additional_projects": projects[1:]}

    calls: list[dict[str, Any]] = []
    pages = [{"entries": [_entry("2026-08-05T12:00:00Z", "boom")]}]
    monkeypatch.setattr(
        module, "build_service", lambda _config, _api: _FakeLoggingService(calls, pages)
    )

    result = gcp_logging_query(
        project="*",
        default_project=projects[0],
        available_projects=projects,
        project_configs=dict.fromkeys(projects, config),
    )

    assert len(calls) == 1
    assert calls[0]["resourceNames"] == [f"projects/{name}" for name in projects]
    assert result["projects"] == projects


def test_logging_query_still_prefers_pod_logs_for_a_live_pod() -> None:
    """Reaching the chat surface must not make this the default log tool.

    ``kubernetes_get_pod_logs`` is faster and needs no Cloud Logging grant, so
    the anti-example has to survive — while still saying when Cloud Logging is
    the right answer, or the model will avoid it for a pod that no longer
    exists.
    """
    registered = get_registered_tool("gcp_logging_query")
    assert registered is not None

    anti = " ".join(registered.anti_examples or ())

    assert "kubernetes_get_pod_logs" in anti
    assert "the pod is gone" in anti
    # The capability that earns it a place on the chat surface.
    use_cases = " ".join(registered.use_cases or ())
    assert "cluster_name" in use_cases and "namespace_name" in use_cases


def test_logging_query_is_on_the_chat_and_action_surfaces() -> None:
    registered = get_registered_tool("gcp_logging_query")
    assert registered is not None

    assert "action" in (registered.surfaces or ())
