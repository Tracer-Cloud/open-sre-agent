"""``gcp_alerting``: what fired, why it was configured to fire, what it protects.

The failure this tool exists to prevent is concluding "nothing is wrong in that
project" from an alert that names an observability project while the workload
runs elsewhere. So the tests that matter most are the ones pinning *which*
project a returned alert is attributed to, and the ones pinning that a
still-open alert survives a lookback window it started before.

Absence-of-telemetry assertions use the ``sentry_events`` fixture rather than
the suite-wide ``OPENSRE_SENTRY_DISABLED``, which would make them pass against a
path that reports on every turn.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any

import pytest

from integrations.gcp.client import MONITORING_API
from integrations.gcp.tools import gcp_alerting_tool
from integrations.gcp.tools.gcp_alerting_tool.alerts import (
    normalize_alert,
    runtime_project_ids,
    select_alerts,
)
from integrations.gcp.tools.gcp_alerting_tool.policies import (
    condition_filters,
    normalize_policy,
)
from integrations.gcp.tools.gcp_alerting_tool.slos import normalize_service, normalize_slo
from tools.registry import get_registered_tool

_SCOPING_ID = "obs-project"
_WORKLOAD_ID = "workload-a"

_POLICY_ID = "1234567890123456789"
_POLICY_NAME = f"projects/{_SCOPING_ID}/alertPolicies/{_POLICY_ID}"

#: Anchored on the real clock, not a frozen literal. Three cases below drive the
#: tool end to end, and the tool reads ``datetime.now(UTC)`` itself — a fixture
#: built from a fixed date ages out of the lookback window a day after it is
#: written, and the test then fails for a reason the product does not have.
_NOW = datetime.now(UTC)

#: The one clause in the description that is *allowed* to name a mutation verb,
#: because it exists to deny it. B2 strips it before scanning the remainder.
_READ_ONLY_CLAUSE = "Read-only: nothing here acknowledges, silences or edits anything."


def _stamp(*, hours_ago: float) -> str:
    return (_NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")


# --- fakes -------------------------------------------------------------------


class _GoogleApiError(Exception):
    """A ``googleapiclient``-shaped error: an HTTP status plus a JSON body.

    ``api_not_enabled`` reads ``ErrorInfo.reason`` out of that body and nothing
    else, so a fake carrying only a message cannot exercise the split between a
    disabled API and an unserved endpoint — which is what B11 and B12 pin.
    """

    def __init__(self, status: HTTPStatus, reason: str = "") -> None:
        super().__init__(f"HTTP {int(status)} from the Google API")
        self.status_code = int(status)
        details = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason}]
        self.content = json.dumps(
            {
                "error": {
                    "code": int(status),
                    "message": "the caller does not have permission",
                    "details": details if reason else [],
                }
            }
        ).encode("utf-8")


class _Executable:
    """A discovery request object: ``execute()`` returns a page or raises."""

    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def execute(self) -> dict[str, Any]:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload if isinstance(self._payload, dict) else {}


class _Recorder:
    """One discovery method: records the exact kwargs it was handed.

    ``**kwargs`` rather than a fixed signature so that an added server-side
    ``filter=`` is *recorded* and asserted on, instead of raising a TypeError
    that reads like a broken fake.
    """

    def __init__(self, calls: list[tuple[str, dict[str, Any]]], label: str, payload: Any) -> None:
        self._calls = calls
        self._label = label
        self._payload = payload

    def __call__(self, **kwargs: Any) -> _Executable:
        self._calls.append((self._label, dict(kwargs)))
        payload = self._payload
        if callable(payload):
            payload = payload(kwargs)
        return _Executable(payload)


class _FakeAlerts:
    def __init__(self, calls: list[tuple[str, dict[str, Any]]], payload: Any) -> None:
        self.list = _Recorder(calls, "alerts.list", payload)


class _FakePolicies:
    def __init__(self, calls: list[tuple[str, dict[str, Any]]], payload: Any) -> None:
        self.list = _Recorder(calls, "alertPolicies.list", payload)
        self.get = _Recorder(calls, "alertPolicies.get", payload)


class _FakeProjects:
    def __init__(self, calls: list[tuple[str, dict[str, Any]]], spec: dict[str, Any]) -> None:
        self._calls = calls
        self._spec = spec

    def alerts(self) -> _FakeAlerts:
        return _FakeAlerts(self._calls, self._spec.get("alerts"))

    def alertPolicies(self) -> _FakePolicies:  # noqa: N802 — Google API method name
        return _FakePolicies(self._calls, self._spec.get("policies"))


class _FakeObjectives:
    def __init__(
        self, calls: list[tuple[str, dict[str, Any]]], per_service: dict[str, Any]
    ) -> None:
        self._calls = calls
        self._per_service = per_service

    def list(self, **kwargs: Any) -> _Executable:
        self._calls.append(("slo.list", dict(kwargs)))
        return _Executable(self._per_service.get(str(kwargs.get("parent", "")), {}))


class _FakeServices:
    def __init__(self, calls: list[tuple[str, dict[str, Any]]], spec: dict[str, Any]) -> None:
        self._calls = calls
        self._spec = spec
        self.list = _Recorder(calls, "services.list", spec.get("services"))

    def serviceLevelObjectives(self) -> _FakeObjectives:  # noqa: N802 — Google API method name
        return _FakeObjectives(self._calls, self._spec.get("objectives") or {})


class _FakeMonitoring:
    def __init__(self, calls: list[tuple[str, dict[str, Any]]], spec: dict[str, Any]) -> None:
        self._calls = calls
        self._spec = spec

    def projects(self) -> _FakeProjects:
        return _FakeProjects(self._calls, self._spec)

    def services(self) -> _FakeServices:
        return _FakeServices(self._calls, self._spec)


class _Harness:
    """Stands in for ``build_service``, recording which API was asked for."""

    def __init__(self, spec: dict[str, Any]) -> None:
        self.apis: list[tuple[str, str]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._spec = spec

    def build_service(self, _config: Any, api: tuple[str, str]) -> Any:
        self.apis.append(api)
        if api != MONITORING_API:
            raise AssertionError(f"gcp_alerting asked for an API it must never build: {api}")
        return _FakeMonitoring(self.calls, self._spec)

    def kwargs_for(self, label: str) -> list[dict[str, Any]]:
        return [call[1] for call in self.calls if call[0] == label]


class _Reports:
    """Records every ``report_run_error`` the tool makes, with its severity."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, exc: BaseException, **kwargs: Any) -> None:
        self.calls.append({"exc": exc, **kwargs})


def _install(monkeypatch: pytest.MonkeyPatch, **spec: Any) -> _Harness:
    harness = _Harness(spec)
    monkeypatch.setattr(gcp_alerting_tool, "build_service", harness.build_service)
    return harness


def _record_reports(monkeypatch: pytest.MonkeyPatch) -> _Reports:
    reports = _Reports()
    monkeypatch.setattr(gcp_alerting_tool, "report_run_error", reports)
    return reports


def _call(**kwargs: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "project": _SCOPING_ID,
        "default_project": _SCOPING_ID,
        "available_projects": [_SCOPING_ID, _WORKLOAD_ID],
    }
    payload.update(kwargs)
    return gcp_alerting_tool.gcp_alerting(**payload)


# --- payload builders ---------------------------------------------------------


def _raw_alert(
    *,
    alert_id: str = "0.abc123",
    state: str = "OPEN",
    open_time: str | None = None,
    close_time: str = "",
    display_name: str = "Checkout latency burn",
    resource_project: str = _WORKLOAD_ID,
) -> dict[str, Any]:
    return {
        "name": f"projects/{_SCOPING_ID}/alerts/{alert_id}",
        "state": state,
        "openTime": _stamp(hours_ago=1) if open_time is None else open_time,
        "closeTime": close_time,
        "policy": {
            "name": _POLICY_NAME,
            "displayName": display_name,
            "severity": "CRITICAL",
            "userLabels": {"team": "checkout"},
        },
        "resource": {
            "type": "k8s_container",
            "labels": {"project_id": resource_project, "namespace_name": "checkout"},
        },
        "metric": {
            "type": "kubernetes.io/container/cpu/request_utilization",
            "labels": {"container_name": "api"},
        },
        "log": {"extractedLabels": {}},
    }


def _threshold_policy(
    *,
    policy_id: str = _POLICY_ID,
    display_name: str = "Checkout latency burn",
    condition_filter: str = 'metric.type="run.googleapis.com/request_latencies"',
    enabled: bool = True,
    invalid_message: str = "",
) -> dict[str, Any]:
    policy: dict[str, Any] = {
        "name": f"projects/{_SCOPING_ID}/alertPolicies/{policy_id}",
        "displayName": display_name,
        "enabled": enabled,
        "severity": "CRITICAL",
        "combiner": "OR",
        "conditions": [
            {
                "displayName": "p99 above 500ms",
                "conditionThreshold": {
                    "filter": condition_filter,
                    "comparison": "COMPARISON_GT",
                    "thresholdValue": 0.5,
                    "duration": "300s",
                    "aggregations": [
                        {
                            "perSeriesAligner": "ALIGN_PERCENTILE_99",
                            "crossSeriesReducer": "REDUCE_MAX",
                            "groupByFields": ["resource.label.namespace_name"],
                        }
                    ],
                },
            }
        ],
        "notificationChannels": [
            f"projects/{_SCOPING_ID}/notificationChannels/1",
            f"projects/{_SCOPING_ID}/notificationChannels/2",
        ],
    }
    if invalid_message:
        policy["validity"] = {"message": invalid_message}
    return policy


def _service(name: str, display: str) -> dict[str, Any]:
    return {
        "name": f"projects/{_SCOPING_ID}/services/{name}",
        "displayName": display,
        "gkeWorkload": {},
    }


def _objective(service_name: str, slo_id: str, goal: float = 0.999) -> dict[str, Any]:
    return {
        "name": f"projects/{_SCOPING_ID}/services/{service_name}/serviceLevelObjectives/{slo_id}",
        "displayName": "99.9% availability",
        "goal": goal,
        "rollingPeriod": "2419200s",
        "serviceLevelIndicator": {"basicSli": {"availability": {}}},
    }


# --- B1-B2: registration and the honest claim --------------------------------


def test_tool_is_registered_and_project_is_not_injected() -> None:
    registered = get_registered_tool("gcp_alerting")

    assert registered is not None
    assert registered.source == "gcp"
    # Injected params overwrite model input, so declaring `project` here would
    # make the schema parameter inert — the Kubernetes `context` bug exactly.
    assert "project" not in (registered.injected_params or ())


def test_the_description_does_not_promise_more_than_the_api_gives() -> None:
    registered = get_registered_tool("gcp_alerting")
    assert registered is not None

    description = registered.description
    # The disclaimer must be there...
    assert _READ_ONLY_CLAUSE in description
    # ...and it is the only place a mutation verb may appear. Scanning the whole
    # string cannot work: the disclaimer itself contains "acknowledges" and
    # "silences", so a naive substring check is either vacuous or always red.
    remainder = description.replace(_READ_ONLY_CLAUSE, "").lower()
    for verb in ("acknowledge", "silence", "snooze", "resolve", "mute"):
        assert verb not in remainder, f"description offers to {verb}, which the API cannot do"


# --- B3-B7: the alerts call and the client-side filters -----------------------


def test_alerts_are_listed_newest_first_with_no_server_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _install(monkeypatch, alerts={"alerts": [_raw_alert()]})

    result = _call(action="alerts")

    assert result["found"] is True
    (kwargs,) = harness.kwargs_for("alerts.list")
    # A server-side filter is the one thing that can return silently empty
    # instead of 400, which is the exact failure this tool exists to fix.
    assert set(kwargs) == {"parent", "pageSize"}
    assert kwargs["parent"] == f"projects/{_SCOPING_ID}"


def test_open_alerts_are_kept_and_closed_ones_dropped() -> None:
    rows = [
        normalize_alert(_raw_alert(alert_id="open-one", state="OPEN")),
        normalize_alert(
            _raw_alert(alert_id="closed-one", state="CLOSED", close_time=_stamp(hours_ago=2))
        ),
    ]

    kept = select_alerts(rows, "open", 24.0, "", now=_NOW)

    assert [row["id"] for row in kept] == ["open-one"]


def test_a_long_running_open_alert_survives_the_window() -> None:
    # Opened five days ago, still open, 24h lookback. Filtering this out is the
    # single worst bug available here: it is the alert that has been firing all
    # incident, and the one the responder most needs.
    row = normalize_alert(
        _raw_alert(alert_id="long-runner", state="OPEN", open_time=_stamp(hours_ago=120))
    )

    kept = select_alerts([row], "open", 24.0, "", now=_NOW)

    assert [entry["id"] for entry in kept] == ["long-runner"]


def test_an_alert_closed_inside_the_window_is_kept() -> None:
    # Opened well before the window, closed inside it: "what fired and recovered
    # during the incident" must still find it.
    row = normalize_alert(
        _raw_alert(
            alert_id="recovered",
            state="CLOSED",
            open_time=_stamp(hours_ago=240),
            close_time=_stamp(hours_ago=1),
        )
    )

    kept = select_alerts([row], "any", 24.0, "", now=_NOW)

    assert [entry["id"] for entry in kept] == ["recovered"]


def test_an_unparseable_timestamp_keeps_the_alert() -> None:
    # Never silently drop evidence over a timestamp shape the parser did not
    # expect — an undated alert is still an alert.
    row = normalize_alert(
        _raw_alert(alert_id="undated", state="CLOSED", open_time="not-a-timestamp", close_time="?")
    )

    kept = select_alerts([row], "any", 24.0, "", now=_NOW)

    assert [entry["id"] for entry in kept] == ["undated"]


# --- B8-B10: placing the alert ------------------------------------------------


def test_the_runtime_project_is_lifted_out_of_the_resource_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, alerts={"alerts": [_raw_alert(resource_project=_WORKLOAD_ID)]})

    result = _call(action="alerts")

    # The alert carries the runtime project in its resource labels. Not
    # surfacing it is what makes the agent answer "that project has nothing".
    assert result["alerts"][0]["resource_project_id"] == _WORKLOAD_ID
    assert result["runtime_project_ids"] == [_WORKLOAD_ID]


def test_a_cross_project_result_says_so_in_the_note(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, alerts={"alerts": [_raw_alert(resource_project=_WORKLOAD_ID)]})

    result = _call(action="alerts")

    # Right data, wrong conclusion is still wrong: the note is what stops the
    # model reading a cross-project hit as "not registered here".
    assert _WORKLOAD_ID in result["note"]
    assert "metrics scope working as intended" in result["note"]


def test_the_policy_that_generated_an_alert_is_identified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, alerts={"alerts": [_raw_alert()]})

    result = _call(action="alerts")

    row = result["alerts"][0]
    # Without policy_id the alerts -> policies handoff has nothing to hand off.
    assert row["policy"] == "Checkout latency burn"
    assert row["policy_id"] == _POLICY_ID
    assert row["policy_project"] == _SCOPING_ID
    assert row["severity"] == "CRITICAL"


# --- B11-B12: degradation without telemetry -----------------------------------


def test_alerts_unavailable_degrades_without_telemetry_and_names_the_fallback(
    monkeypatch: pytest.MonkeyPatch,
    sentry_events: list[BaseException],
) -> None:
    _install(monkeypatch, alerts=_GoogleApiError(HTTPStatus.NOT_FOUND))

    result = _call(action="alerts")

    # Alert instances come from a newer endpoint than alert policies. Where an
    # estate does not serve them this is a capability gap, not a fault, and
    # reporting would file one Sentry error per turn forever.
    assert result["available"] is False
    assert "action='policies'" in result["error"]
    assert sentry_events == []


def test_a_disabled_monitoring_api_degrades_without_telemetry(
    monkeypatch: pytest.MonkeyPatch,
    sentry_events: list[BaseException],
) -> None:
    _install(
        monkeypatch,
        alerts=_GoogleApiError(HTTPStatus.FORBIDDEN, reason="SERVICE_DISABLED"),
    )

    result = _call(action="alerts")

    # Distinct from the unserved-endpoint case above, and the wording has to
    # say so: one is "switch the API on", the other is "use another action".
    assert result["available"] is False
    assert result["api_enabled"] is False
    assert "not enabled" in result["error"]
    assert sentry_events == []


# --- B13-B17: policies --------------------------------------------------------


def test_policies_report_the_condition_filter_and_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, policies={"alertPolicies": [_threshold_policy()]})

    result = _call(action="policies")

    condition = result["policies"][0]["conditions"][0]
    # A bare display name does not tell the agent what the alert was built on.
    assert condition["kind"] == "threshold"
    assert condition["filter"] == 'metric.type="run.googleapis.com/request_latencies"'
    assert condition["comparison"] == "COMPARISON_GT"
    assert condition["threshold"] == 0.5
    assert condition["duration"] == "300s"


def test_condition_filters_are_flattened_and_deduplicated() -> None:
    shared = 'metric.type="run.googleapis.com/request_latencies"'
    other = 'metric.type="run.googleapis.com/request_count"'
    policies = [
        normalize_policy(_threshold_policy(policy_id="1", condition_filter=shared)),
        normalize_policy(_threshold_policy(policy_id="2", condition_filter=shared)),
        normalize_policy(_threshold_policy(policy_id="3", condition_filter=other)),
    ]

    # Paste-ready into gcp_monitoring_query: the agent re-runs the alert's own
    # query rather than inventing an approximation of it.
    assert condition_filters(policies) == [shared, other]


def test_an_mql_condition_uses_query_not_filter() -> None:
    policy = normalize_policy(
        {
            "name": _POLICY_NAME,
            "displayName": "MQL policy",
            "conditions": [
                {
                    "displayName": "mql condition",
                    "conditionMonitoringQueryLanguage": {
                        "query": "fetch k8s_container | every 1m",
                        "duration": "60s",
                    },
                }
            ],
        }
    )

    condition = policy["conditions"][0]
    # Falling through to `unknown` renders a non-threshold condition as an empty
    # threshold, which reads as a policy with no configuration at all.
    assert condition["kind"] == "mql"
    assert condition["query"] == "fetch k8s_container | every 1m"
    assert condition["filter"] == ""


def test_a_disabled_or_invalid_policy_is_reported_as_such(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        policies={
            "alertPolicies": [
                _threshold_policy(enabled=False, invalid_message="condition filter is invalid")
            ]
        },
    )

    result = _call(action="policies")

    row = result["policies"][0]
    # "Nothing is wrong" concluded from a policy that physically cannot fire is
    # the quiet version of this tool's core failure.
    assert row["enabled"] is False
    assert row["invalid_reason"] == "condition filter is invalid"
    assert "disabled" in result["note"]


def test_a_policy_is_fetchable_by_the_id_an_alert_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _install(monkeypatch, policies=_threshold_policy())

    result = _call(action="policies", policy_id=_POLICY_ID)

    assert result["found"] is True
    # Echoed back on every policies answer, so a caller that fired several
    # lookups can tell which id this one is about — and so the empty-listing
    # shape and the found-it shape agree on their keys.
    assert result["policy_id"] == _POLICY_ID
    # Without the get path the alerts -> policies loop never closes: the agent
    # has an id and no way to spend it.
    (kwargs,) = harness.kwargs_for("alertPolicies.get")
    assert kwargs["name"] == f"projects/{_SCOPING_ID}/alertPolicies/{_POLICY_ID}"
    assert harness.kwargs_for("alertPolicies.list") == []


def test_an_id_the_project_no_longer_holds_is_an_answer_not_a_permission_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """404 on a single-resource get means "no such policy", not "no such grant".

    This is the ordinary case on the loop this path exists to close: an alert
    names the policy that raised it, the policy has since been deleted, and the
    agent spends the id. Falling through to the unentitled branch answers
    ``api_enabled: false`` with prose about granting ``monitoring.viewer`` —
    sending the reader to fix an IAM posture that is already correct.
    """
    # Arrange.
    _install(monkeypatch, policies=_GoogleApiError(HTTPStatus.NOT_FOUND))
    reports = _record_reports(monkeypatch)

    # Act.
    result = _call(action="policies", policy_id=_POLICY_ID)

    # Assert: an empty listing, and the id it is about.
    assert result["found"] is True
    assert result["policy_count"] == 0
    assert result["policies"] == []
    assert result["policy_id"] == _POLICY_ID
    assert _POLICY_ID in result["note"]
    assert "deleted" in result["note"]
    # Not the entitlement shape, and not telemetry either — nothing failed.
    assert result.get("api_enabled") is not False
    assert reports.calls == []


def test_a_denied_policy_read_is_still_an_entitlement_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 404 branch is ranked first, so the 403 it sits above needs its own pin.

    Widening that branch to every unentitled status would turn a genuinely
    ungranted read into "the policy was most likely deleted" — advice that sends
    the reader looking for a policy that is there and that they cannot see.
    """
    # Arrange.
    _install(monkeypatch, policies=_GoogleApiError(HTTPStatus.FORBIDDEN))

    # Act.
    result = _call(action="policies", policy_id=_POLICY_ID)

    # Assert.
    assert result["available"] is False
    assert result["api_enabled"] is False


# --- B18-B19: SLOs ------------------------------------------------------------


def test_slos_enumerate_services_then_objectives_and_report_goal_and_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _service("checkout", "Checkout")
    harness = _install(
        monkeypatch,
        services={"services": [checkout]},
        objectives={
            checkout["name"]: {
                "serviceLevelObjectives": [_objective("checkout", "checkout-availability")]
            }
        },
    )

    result = _call(action="slos")

    # 1+N, deliberately: the services/- wildcard is documented only under the
    # workspaces/ form, and a wildcard that silently returns nothing is worse
    # than N calls that always work.
    assert [call[0] for call in harness.calls] == ["services.list", "slo.list"]
    (slo_kwargs,) = harness.kwargs_for("slo.list")
    assert slo_kwargs["parent"] == checkout["name"]
    assert slo_kwargs["view"] == "FULL"

    row = result["slos"][0]
    assert row["goal"] == 0.999
    assert row["period"] == "rolling 2419200s"
    assert row["sli_kind"] == "basic_availability"


def test_one_failing_service_does_not_lose_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout, payments, billing, search = (
        _service("checkout", "Checkout"),
        _service("payments", "Payments"),
        _service("billing", "Billing"),
        _service("search", "Search"),
    )
    _install(
        monkeypatch,
        services={"services": [checkout, payments, billing, search]},
        objectives={
            checkout["name"]: {"serviceLevelObjectives": [_objective("checkout", "checkout-slo")]},
            payments["name"]: _GoogleApiError(HTTPStatus.FORBIDDEN, reason="IAM_PERMISSION_DENIED"),
            billing["name"]: _GoogleApiError(
                HTTPStatus.INTERNAL_SERVER_ERROR, reason="backendError"
            ),
            search["name"]: {"serviceLevelObjectives": [_objective("search", "search-slo")]},
        },
    )
    reports = _record_reports(monkeypatch)

    result = _call(action="slos")

    # Two unreadable services must not discard every SLO in the project.
    assert result["slo_count"] == 2
    assert [row["id"] for row in result["slos"]] == ["checkout-slo", "search-slo"]

    # Both gaps are reported to the reader, whatever their cause.
    assert len(result["partial_errors"]) == 2
    assert "Payments" in result["partial_errors"][0]
    assert "Billing" in result["partial_errors"][1]

    # But only the 500 is telemetry. A 403 is the operator's IAM posture, and
    # reporting it files one Sentry event per turn for as long as it stands.
    assert len(reports.calls) == 1
    assert reports.calls[0]["severity"] == "warning"
    assert reports.calls[0]["extras"]["service"] == billing["name"]


# --- helper-level coverage the tool paths above do not reach ------------------


def test_runtime_project_ids_are_distinct_and_first_seen_ordered() -> None:
    rows = [
        normalize_alert(_raw_alert(alert_id="a", resource_project=_WORKLOAD_ID)),
        normalize_alert(_raw_alert(alert_id="b", resource_project=_SCOPING_ID)),
        normalize_alert(_raw_alert(alert_id="c", resource_project=_WORKLOAD_ID)),
        normalize_alert(_raw_alert(alert_id="d", resource_project="")),
    ]

    assert runtime_project_ids(rows) == [_WORKLOAD_ID, _SCOPING_ID]


def test_the_alert_count_is_the_match_total_not_the_returned_slice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        alerts={"alerts": [_raw_alert(alert_id=f"open-{index}") for index in range(3)]},
    )

    result = _call(action="alerts", limit=1)

    # Counting after the slice is how "3 fired" becomes "1 fired" — the exact
    # shape of a bug this repo has already shipped once.
    assert result["alert_count"] == 3
    assert len(result["alerts"]) == 1


def test_a_service_kind_is_reported_from_whichever_oneof_is_set() -> None:
    row = normalize_slo(
        _objective("checkout", "checkout-slo"),
        normalize_service(_service("checkout", "Checkout")),
    )

    assert row["service_kind"] == "gke_workload"
    assert row["service_display_name"] == "Checkout"
    assert row["service"] == "checkout"


def test_an_unknown_action_is_refused_with_every_result_key_present() -> None:
    result = _call(action="sideways")

    # A consumer must never key-error on the shape of an action it did not ask
    # for, including on the refusal path.
    assert result["available"] is False
    assert result["alerts"] == []
    assert result["policies"] == []
    assert result["slos"] == []


def test_more_than_one_project_is_refused_rather_than_silently_narrowed() -> None:
    result = _call(project=f"{_SCOPING_ID},{_WORKLOAD_ID}")

    assert result["available"] is False
    assert "one metrics scope per call" in result["error"]


# --- B20-B26: bounded reads, the wire, and what never reaches the payload -----


def test_a_policy_named_on_a_later_page_is_still_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``name_contains`` filters client-side, so page one is not the estate.

    An alert names its policy by id, but an operator names it by display name.
    Stopping at page one answers "no such policy" for every policy Google
    happens to list second — a wrong answer that reads exactly like a right one.
    """
    # Arrange: 300 policies over two pages; the wanted one is on the second.
    policies = [
        _threshold_policy(policy_id=str(index), display_name=f"Policy {index}")
        for index in range(300)
    ]

    def two_pages(kwargs: dict[str, Any]) -> dict[str, Any]:
        if not kwargs.get("pageToken"):
            return {"alertPolicies": policies[:250], "nextPageToken": "page-2", "totalSize": 300}
        return {"alertPolicies": policies[250:], "totalSize": 300}

    harness = _install(monkeypatch, policies=two_pages)

    # Act.
    result = _call(action="policies", name_contains="Policy 275", limit=10)

    # Assert: found, and the second request carried the token it was handed.
    assert result["policy_count"] == 1
    assert result["policies"][0]["display_name"] == "Policy 275"
    assert result["truncated"] is False
    assert [kw.get("pageToken") for kw in harness.kwargs_for("alertPolicies.list")] == [
        None,
        "page-2",
    ]


def test_a_search_that_ran_out_of_pages_does_not_claim_the_policy_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Walking pages has to stop somewhere, and stopping is not the same as absent.

    Without the note, an estate larger than the page cap turns "I did not get
    that far" into "it does not exist" — the failure the paging fix exists to
    prevent, one page cap further out.
    """

    # Arrange: every page hands back another token and nothing ever matches.
    def endless_pages(kwargs: dict[str, Any]) -> dict[str, Any]:
        page = int(kwargs.get("pageToken") or "0")
        return {
            "alertPolicies": [
                _threshold_policy(policy_id=str(page * 100 + index), display_name=f"Other {index}")
                for index in range(100)
            ],
            "nextPageToken": str(page + 1),
            "totalSize": 1000,
        }

    _install(monkeypatch, policies=endless_pages)

    # Act.
    result = _call(action="policies", name_contains="Checkout latency", limit=10)

    # Assert: no match, and the answer says why it cannot rule the policy out.
    assert result["policy_count"] == 0
    assert result["truncated"] is True
    assert "capped after" in result["note"]
    assert "did not cover the whole estate" in result["note"]


def test_a_normalized_policy_carries_no_email_and_no_channel_names() -> None:
    """The shape is an allowlist, so a field Google adds later cannot ride along.

    ``mutationRecord.mutatedBy`` is documented as the email of whoever last
    edited the policy, and ``notificationChannels`` names the pager rotations.
    Neither answers "why did this fire", and both land in a chat channel.
    Asserted as set *equality* rather than absence: a denylist would need
    updating every time the API grows a field, and nobody would notice it had not.
    """
    # Arrange: a policy carrying both.
    policy = _threshold_policy()
    policy["mutationRecord"] = {
        "mutatedBy": "someone@example.com",
        "mutateTime": "2026-08-01T12:00:00Z",
    }

    # Act.
    result = normalize_policy(policy)

    # Assert: the exact key set, the count in place of the names, no email.
    assert set(result) == {
        "id",
        "display_name",
        "enabled",
        "severity",
        "combiner",
        "condition_count",
        "conditions",
        "notification_channel_count",
        "auto_close",
        "documentation_subject",
        "user_labels",
        "invalid_reason",
        "last_modified_at",
    }
    assert result["notification_channel_count"] == 2
    assert "someone@example.com" not in json.dumps(result)
    assert result["last_modified_at"] == "2026-08-01T12:00:00Z"


def test_each_list_call_uses_the_path_parameter_that_resource_actually_takes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``alerts.list`` takes ``parent``; ``alertPolicies.list`` takes ``name``.

    The two sit one line apart in the same file and disagree — Google 400s on
    the wrong one. Also pins the absence of ``orderBy``: the reference documents
    the server default as ``openTime desc`` already, and the camelCase field
    names are not the snake_case ones the first draft sent.
    """
    # Arrange.
    harness = _install(
        monkeypatch,
        alerts={"alerts": [_raw_alert()]},
        policies={"alertPolicies": [_threshold_policy()]},
    )

    # Act.
    _call(action="alerts")
    _call(action="policies")

    # Assert.
    (alert_kwargs,) = harness.kwargs_for("alerts.list")
    assert alert_kwargs["parent"] == f"projects/{_SCOPING_ID}"
    assert "name" not in alert_kwargs
    assert "orderBy" not in alert_kwargs

    (policy_kwargs,) = harness.kwargs_for("alertPolicies.list")
    assert policy_kwargs["name"] == f"projects/{_SCOPING_ID}"
    assert "parent" not in policy_kwargs


@pytest.mark.parametrize(
    ("action", "failing"),
    [("policies", "policies"), ("slos", "services")],
)
def test_a_disabled_monitoring_api_degrades_on_every_action_without_telemetry(
    monkeypatch: pytest.MonkeyPatch,
    sentry_events: list[BaseException],
    action: str,
    failing: str,
) -> None:
    """A project that never turned the API on is configuration, not a fault.

    Reporting it would file one Sentry error per turn for as long as the project
    stays that way. The alerts path already degrades; these two reach the API
    through different resources, so each needs its own branch.
    """
    # Arrange.
    _install(monkeypatch, **{failing: _GoogleApiError(HTTPStatus.FORBIDDEN, "SERVICE_DISABLED")})

    # Act.
    result = _call(action=action)

    # Assert.
    assert result["available"] is False
    assert result["api_enabled"] is False
    assert "not enabled" in result["error"]
    assert sentry_events == []


@pytest.mark.parametrize(
    ("action", "failing", "fallback"),
    [
        ("alerts", "alerts", "policies"),
        ("policies", "policies", "alerts"),
        ("slos", "services", "policies"),
    ],
)
@pytest.mark.parametrize(
    "status",
    [HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND, HTTPStatus.NOT_IMPLEMENTED],
)
def test_a_read_the_grants_do_not_reach_is_unentitled_not_broken(
    monkeypatch: pytest.MonkeyPatch,
    sentry_events: list[BaseException],
    action: str,
    failing: str,
    fallback: str,
    status: HTTPStatus,
) -> None:
    """A permanent entitlement gap must not report, or it reports every turn.

    ``projects.alerts`` is newer than the rest of v3 and not on every account,
    but the same is true of any of the three: an estate can grant
    ``monitoring.alertPolicies.list`` and withhold ``monitoring.services.list``.
    Each action reaches the API through a different resource, so a branch on one
    of them says nothing about the other two — which is exactly how the first
    version of this tool shipped with the ladder on ``alerts`` alone.

    The message also has to send the reader to a sibling action rather than to a
    support ticket: the gap is in their IAM, and part of the question is usually
    still answerable.
    """
    # Arrange.
    _install(monkeypatch, **{failing: _GoogleApiError(status, "IAM_PERMISSION_DENIED")})

    # Act.
    result = _call(action=action)

    # Assert.
    assert result["available"] is False
    assert result["api_enabled"] is False
    assert fallback in result["error"]
    assert sentry_events == []


@pytest.mark.parametrize(
    ("action", "label", "cap"),
    [
        ("alerts", "alerts.list", 200),
        ("policies", "alertPolicies.list", 200),
        ("slos", "services.list", 25),
    ],
)
def test_a_huge_max_results_cannot_widen_the_page_the_tool_asks_for(
    monkeypatch: pytest.MonkeyPatch, action: str, label: str, cap: int
) -> None:
    """``max_results`` is an instance-config value, so it is not the model's to spend.

    A chat turn carries one tool payload into the context window. ``limit``
    reaches the tool already clamped, and every one of the three reads has to
    honour that clamp — a path that passed the raw value through would ask
    Google for 5000 rows on an estate whose config says that is allowed.
    """
    # Arrange: an empty estate, so only the request kwargs matter.
    harness = _install(
        monkeypatch,
        alerts={"alerts": []},
        policies={"alertPolicies": []},
        services={"services": []},
    )

    # Act: ask for far more than any cap.
    _call(action=action, limit=5000, max_results=5000)

    # Assert.
    assert harness.kwargs_for(label)[0]["pageSize"] == cap


def test_a_full_alert_page_says_so_even_when_the_rows_fit_under_the_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``truncated`` reports the *server* page, not the client-side slice.

    The state filter runs client-side, so a page that filled up server-side can
    still hand back three rows against a limit of 100 — and older matches beyond
    it exist. Inferring truncation from the row count would call that complete.
    """
    # Arrange: one row, but the server says there is more behind it.
    _install(
        monkeypatch,
        alerts={
            "alerts": [_raw_alert()],
            "nextPageToken": "page-2",
            "totalSize": 412,
        },
    )

    # Act.
    result = _call(action="alerts")

    # Assert: the answer is flagged partial and says how much it did not reach.
    assert result["truncated"] is True
    assert result["total_size"] == 412
    assert result["alert_count"] == 1


def test_a_service_whose_objectives_ran_past_one_page_flags_the_slo_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SLO truncation is per service, and the services listing cannot show it.

    ``services_truncated`` covers the outer 1+N call; a service that owns more
    objectives than one page holds is invisible to it, so "these are the SLOs"
    would read as exhaustive while a burning one sat on page two.
    """
    # Arrange: the services listing is complete; the objectives listing is not.
    checkout = _service("checkout", "Checkout")
    _install(
        monkeypatch,
        services={"services": [checkout]},
        objectives={
            checkout["name"]: {
                "serviceLevelObjectives": [_objective("checkout", "checkout-availability")],
                "nextPageToken": "page-2",
            }
        },
    )

    # Act.
    result = _call(action="slos")

    # Assert: the inner truncation is reported without claiming the outer one.
    assert result["slos_truncated"] is True
    assert result["services_truncated"] is False


def test_a_page_of_disabled_policies_does_not_condemn_the_ones_behind_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "Every matching policy is disabled" is a claim about the estate, not the slice.

    ``limit`` is a display width. Reading the enabled flags off the sliced rows
    lets two disabled policies at the top of the list tell an operator that
    nothing in the project can fire, while the policy that would have caught
    their incident sits one row past the cut — the worst possible false
    negative for a tool whose whole job is "would this have paged anyone?".
    """
    # Arrange: the two the caller will see are disabled; a third is not.
    _install(
        monkeypatch,
        policies={
            "alertPolicies": [
                _threshold_policy(policy_id="1", display_name="Dark one", enabled=False),
                _threshold_policy(policy_id="2", display_name="Dark two", enabled=False),
                _threshold_policy(policy_id="3", display_name="Live one", enabled=True),
            ]
        },
    )

    # Act: ask for fewer rows than there are matches.
    result = _call(action="policies", limit=2)

    # Assert: the slice is disabled, the estate is not, and the note says so.
    assert [row["display_name"] for row in result["policies"]] == ["Dark one", "Dark two"]
    assert result["policy_count"] == 3
    assert result["note"] == ""


def test_a_capped_search_says_so_even_when_it_did_find_something(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partial answer that reads as complete is worse than no answer.

    ``name_contains`` is applied client-side over at most five pages, so a
    search that returns three hits may still have three more it never reached.
    Gating the warning on having found *nothing* tells the operator the estate
    was fully covered in exactly the case where they are most likely to act on
    the result.
    """

    # Arrange: every page matches and every page has another behind it.
    def _endless(kwargs: dict[str, Any]) -> dict[str, Any]:
        token = str(kwargs.get("pageToken") or "0")
        return {
            "alertPolicies": [_threshold_policy(policy_id=token, display_name=f"latency {token}")],
            "nextPageToken": str(int(token) + 1),
            "totalSize": 900,
        }

    _install(monkeypatch, policies=_endless)

    # Act.
    result = _call(action="policies", name_contains="latency")

    # Assert: hits found, and the answer still admits it is partial.
    assert result["policy_count"] == 5
    assert result["truncated"] is True
    # The depth is the actionable half of the note: "capped after 5 pages" tells
    # the operator to narrow the filter, and a count that is not the real one
    # sends them looking for pages six through however-many that were never read.
    assert "capped after 5 pages" in result["note"]


def test_a_search_that_filled_the_limit_on_page_one_is_not_a_capped_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``truncated`` means "the server had more", which is not the same stop.

    A search that reached ``limit`` on the first page stopped because of the
    caller's display width, and the fix is to raise ``limit``. Reporting that as
    "capped after 1 pages" blames the page budget and points the reader at
    narrowing the filter — the opposite of what would help.
    """

    # Arrange: every page matches and every page has another behind it.
    def _endless(kwargs: dict[str, Any]) -> dict[str, Any]:
        token = str(kwargs.get("pageToken") or "0")
        return {
            "alertPolicies": [_threshold_policy(policy_id=token, display_name=f"latency {token}")],
            "nextPageToken": str(int(token) + 1),
        }

    _install(monkeypatch, policies=_endless)

    # Act: one row is all the caller asked for, so page one satisfies it.
    result = _call(action="policies", name_contains="latency", limit=1)

    # Assert: still honestly truncated, but not blamed on the page cap.
    assert result["policy_count"] == 1
    assert result["truncated"] is True
    assert "capped" not in result["note"]


def test_a_capped_search_that_found_only_dark_policies_hedges_the_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The disabled note and the capped note are both true; only one can be said.

    Emitting the unqualified "every matching policy is disabled" off a walk that
    stopped early states a fact about pages nobody read.
    """

    # Arrange: five pages, all matching, all disabled, more behind them.
    def _endless(kwargs: dict[str, Any]) -> dict[str, Any]:
        token = str(kwargs.get("pageToken") or "0")
        return {
            "alertPolicies": [
                _threshold_policy(policy_id=token, display_name=f"latency {token}", enabled=False)
            ],
            "nextPageToken": str(int(token) + 1),
        }

    _install(monkeypatch, policies=_endless)

    # Act.
    result = _call(action="policies", name_contains="latency")

    # Assert.
    assert result["truncated"] is True
    assert "found so far" in result["note"]
    assert "capped" in result["note"]


def test_the_estate_size_survives_a_page_that_omits_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``totalSize`` is not repeated on every page, and reassigning loses it.

    Google documents the field on the response, not on every response in a
    sequence. Overwriting the running value with a continuation page's absent
    one reports an estate of zero alert policies while handing back rows from
    it — a contradiction the reader has no way to resolve.
    """
    # Arrange: page one knows the estate size, page two does not repeat it.
    pages: dict[str, dict[str, Any]] = {
        "0": {
            "alertPolicies": [_threshold_policy(policy_id="1", display_name="latency one")],
            "nextPageToken": "1",
            "totalSize": 250,
        },
        "1": {"alertPolicies": [_threshold_policy(policy_id="2", display_name="latency two")]},
    }

    def _paged(kwargs: dict[str, Any]) -> dict[str, Any]:
        return pages[str(kwargs.get("pageToken") or "0")]

    _install(monkeypatch, policies=_paged)

    # Act.
    result = _call(action="policies", name_contains="latency")

    # Assert: both pages were read, and the size page one reported still stands.
    assert result["policy_count"] == 2
    assert result["total_size"] == 250


def test_a_nonsense_lookback_falls_back_to_the_default_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NaN compares false against everything, so every guard in the clamp lets it by.

    ``hours <= 0`` is False for NaN and ``min(nan, cap)`` returns NaN, so the
    value reaches ``timedelta`` intact and raises there — several frames from
    the tool's error handling, as an uncaught fault rather than the fallback the
    clamp exists to provide.
    """
    # Arrange: one alert inside the 24h default, one well outside it.
    _install(
        monkeypatch,
        alerts={
            "alerts": [
                _raw_alert(
                    alert_id="recent",
                    state="CLOSED",
                    open_time=_stamp(hours_ago=2),
                    close_time=_stamp(hours_ago=1),
                ),
                _raw_alert(
                    alert_id="ancient",
                    state="CLOSED",
                    open_time=_stamp(hours_ago=40),
                    close_time=_stamp(hours_ago=39),
                ),
            ]
        },
    )

    # Act.
    result = _call(action="alerts", state="any", hours=float("nan"))

    # Assert: the default window applied, rather than nothing or everything.
    assert result["found"] is True
    assert [row["id"] for row in result["alerts"]] == ["recent"]


def test_an_omitted_enabled_flag_means_the_policy_is_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Google omits ``enabled`` on an enabled policy; only a disabled one says so.

    Reading absence as ``False`` inverts the field for the entire normal case.
    The tool would then report a healthy project as one where nothing can fire,
    and — since that is also the answer the operator is hoping for — it reads as
    a finding rather than as a bug. There is no assertion anywhere else in the
    suite that would notice, because every fixture sets the flag explicitly.
    """
    # Arrange: a policy exactly as Google returns it when it is enabled.
    live = _threshold_policy(policy_id="1", display_name="Live")
    del live["enabled"]
    _install(monkeypatch, policies={"alertPolicies": [live]})

    # Act.
    result = _call(action="policies")

    # Assert: live, and no "nothing here can fire" claim.
    assert result["policies"][0]["enabled"] is True
    assert result["note"] == ""


def test_a_rich_policy_keeps_the_fields_an_operator_acts_on() -> None:
    """The allowlist is only useful if the fields it lets through arrive intact.

    Set-equality on the key names passes just as happily against a shaper that
    returns every one of them blank. These four are the ones a reader acts on:
    the runbook link, the team the policy belongs to, how long an alert stays
    open after the condition clears, and why Google thinks it is broken.
    """
    # Arrange.
    policy = _threshold_policy(invalid_message="the filter references a deleted metric")
    policy["documentation"] = {"subject": "Checkout latency runbook", "content": "..."}
    policy["userLabels"] = {"team": "checkout", "tier": "1"}
    policy["alertStrategy"] = {"autoClose": "1800s"}

    # Act.
    result = normalize_policy(policy)

    # Assert.
    assert result["documentation_subject"] == "Checkout latency runbook"
    assert result["user_labels"] == {"team": "checkout", "tier": "1"}
    assert result["auto_close"] == "1800s"
    assert result["invalid_reason"] == "the filter references a deleted metric"


def test_the_alert_scan_is_wider_than_the_limit_because_the_filter_is_client_side(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Filtering after the fetch starves unless the fetch over-reads.

    ``state`` is applied client-side because the endpoint has no dependable
    server filter. Asking Google for exactly ``limit`` rows and then discarding
    the closed ones returns fewer open alerts than the caller asked for, and
    reports it as though that were all there was. ``state='any'`` discards
    nothing, so it has no reason to over-read.
    """
    # Arrange.
    harness = _install(monkeypatch, alerts={"alerts": []})

    # Act.
    _call(action="alerts", limit=10)
    _call(action="alerts", limit=10, state="any")

    # Assert.
    open_kwargs, any_kwargs = harness.kwargs_for("alerts.list")
    assert open_kwargs["pageSize"] == 40
    assert any_kwargs["pageSize"] == 10


def test_an_empty_open_list_sends_the_reader_to_the_next_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "No open alerts" is the answer most likely to be misread as "nothing is wrong".

    The default state and the default window are both narrow, and an incident
    that closed 30 hours ago satisfies neither. ``scanned`` is the honesty
    counter that separates "the endpoint returned nothing" from "rows came back
    and the filter dropped them all".
    """
    # Arrange: rows exist; none of them are open.
    _install(
        monkeypatch,
        alerts={
            "alerts": [
                _raw_alert(alert_id="a", state="CLOSED", close_time=_stamp(hours_ago=2)),
                _raw_alert(alert_id="b", state="CLOSED", close_time=_stamp(hours_ago=3)),
            ]
        },
    )

    # Act.
    result = _call(action="alerts")

    # Assert.
    assert result["alerts"] == []
    assert result["scanned"] == 2
    assert "state='any'" in result["note"]


@pytest.mark.parametrize("hours", [1_000_000, float("inf")])
def test_a_month_is_as_far_back_as_a_lookback_goes(
    monkeypatch: pytest.MonkeyPatch,
    hours: float,
) -> None:
    """An unbounded window is a cost and latency hazard, not a generous default.

    The clamp is the only thing standing between "hours=1000000" and a scan of
    every alert the project has ever raised, paid for in context on a chat turn.

    ``inf`` is in the parametrize because the NaN guard is one character from
    rejecting it too: ``math.isfinite`` reads as the tidier spelling of
    ``math.isnan`` and silently turns "as far back as you will go" into the 24h
    default — a *narrower* window than asked for, which is the failure mode this
    tool exists to prevent.
    """
    # Arrange: one alert inside 30 days, one outside it.
    _install(
        monkeypatch,
        alerts={
            "alerts": [
                _raw_alert(
                    alert_id="inside",
                    state="CLOSED",
                    open_time=_stamp(hours_ago=29 * 24),
                    close_time=_stamp(hours_ago=29 * 24 - 1),
                ),
                _raw_alert(
                    alert_id="outside",
                    state="CLOSED",
                    open_time=_stamp(hours_ago=40 * 24),
                    close_time=_stamp(hours_ago=40 * 24 - 1),
                ),
            ]
        },
    )

    # Act.
    result = _call(action="alerts", state="any", hours=hours)

    # Assert.
    assert [row["id"] for row in result["alerts"]] == ["inside"]


def test_a_number_python_cannot_coerce_falls_back_instead_of_failing_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both clamps catch ``OverflowError``, from opposite directions.

    ``float()`` refuses an ``int`` too large to represent, and ``json.loads``
    hands one straight through from a long numeric literal. ``int()`` refuses
    ``inf``, and a model can write ``Infinity`` in JSON. Neither raises
    ``ValueError``, so a net without ``OverflowError`` in it loses the whole turn
    for a value both parameters have a perfectly good default for.
    """
    # Arrange: one alert well outside the 24h default window.
    harness = _install(
        monkeypatch,
        alerts={
            "alerts": [
                _raw_alert(
                    alert_id="ancient",
                    state="CLOSED",
                    open_time=_stamp(hours_ago=40),
                    close_time=_stamp(hours_ago=39),
                )
            ]
        },
    )

    # Act.
    hours_result = _call(action="alerts", state="any", hours=10**400)
    limit_result = _call(action="alerts", limit=float("inf"))

    # Assert: the defaults, not an exception.
    assert hours_result["found"] is True
    assert hours_result["alerts"] == []
    assert limit_result["found"] is True
    assert harness.kwargs_for("alerts.list")[1]["pageSize"] == 200


def test_an_unknown_state_is_refused_rather_than_quietly_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo'd state must not silently widen the filter to everything.

    ``select_alerts`` branches on known values, so an unrecognised one falls
    through to whatever the last branch does — and the caller reads the result
    as the filter they asked for.
    """
    # Arrange.
    _install(monkeypatch, alerts={"alerts": [_raw_alert()]})

    # Act.
    result = _call(action="alerts", state="opne")

    # Assert.
    assert result["available"] is False
    assert "opne" in result["error"]


def test_a_nonsense_limit_falls_back_instead_of_failing_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``limit`` is model-supplied, so it arrives as whatever the model wrote.

    An uncaught coercion here costs the whole turn for a value the tool has a
    perfectly good default for.
    """
    # Arrange.
    harness = _install(monkeypatch, alerts={"alerts": []})

    # Act.
    result = _call(action="alerts", limit="as many as you can")

    # Assert: the default limit, over-read by the client-side filter's factor.
    assert result["found"] is True
    assert harness.kwargs_for("alerts.list")[0]["pageSize"] == 200


def test_a_narrow_limit_does_not_narrow_the_policy_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Search depth is not the reader's display width to spend.

    Paging at ``limit`` would let "show me 5 policies matching latency" scan 25
    rows where a default-limit call scans 1000 — and then report the same
    "capped after 5 pages" note, so the two answers are indistinguishable. A
    plain listing has no such problem and still pages at ``limit``.
    """
    # Arrange.
    harness = _install(monkeypatch, policies={"alertPolicies": []})

    # Act.
    _call(action="policies", limit=5, name_contains="latency")
    _call(action="policies", limit=5)

    # Assert.
    search_kwargs, listing_kwargs = harness.kwargs_for("alertPolicies.list")
    assert search_kwargs["pageSize"] == 200
    assert listing_kwargs["pageSize"] == 5


def test_the_slo_list_is_sliced_but_the_count_is_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same contract as ``alert_count``, on the path that did not have it pinned.

    Reporting the truncated length as the total is how a caller concludes "this
    service has two objectives" from a service that has five.
    """
    # Arrange: one service owning more objectives than the caller asked for.
    checkout = _service("checkout", "Checkout")
    _install(
        monkeypatch,
        services={"services": [checkout]},
        objectives={
            checkout["name"]: {
                "serviceLevelObjectives": [
                    _objective("checkout", f"slo-{index}") for index in range(5)
                ]
            }
        },
    )

    # Act.
    result = _call(action="slos", limit=2)

    # Assert.
    assert result["slo_count"] == 5
    assert [row["id"] for row in result["slos"]] == ["slo-0", "slo-1"]


def test_a_services_listing_that_exactly_fills_the_cap_is_flagged_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Google is not obliged to send a page token when the page happens to end.

    Trusting ``nextPageToken`` alone means an estate of exactly the cap reports
    itself complete. Since the cap is also the most likely place for a large
    estate to land, that is the case where "these are all your services" is most
    likely to be wrong.
    """
    # Arrange: the cap, filled exactly, with no token behind it.
    services = [_service(f"svc-{index}", f"Service {index}") for index in range(25)]
    _install(monkeypatch, services={"services": services})

    # Act.
    result = _call(action="slos")

    # Assert.
    assert result["service_count"] == 25
    assert result["services_truncated"] is True


def test_the_objectives_call_is_bounded_per_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SLO read is 1+N, so an unbounded inner page multiplies by N.

    One turn carries one payload. Twenty-five services each free to return
    every objective they own is the shape that produced the 14 MB pod-log turn.
    """
    # Arrange.
    checkout = _service("checkout", "Checkout")
    harness = _install(monkeypatch, services={"services": [checkout]})

    # Act.
    _call(action="slos", limit=7)

    # Assert.
    assert harness.kwargs_for("slo.list")[0]["pageSize"] == 7


def test_the_policy_response_hands_back_the_filters_ready_to_re_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helper being right says nothing about the response carrying it.

    ``condition_filters`` is the paste-into-gcp_monitoring_query path — the
    whole reason to read policy configuration rather than just count it. Drop
    the key from the payload and every helper-level test stays green while the
    agent is left to invent an approximation of the alert's own query.
    """
    # Arrange: two policies sharing one filter, plus a third with its own.
    shared = 'metric.type="run.googleapis.com/request_latencies"'
    other = 'metric.type="run.googleapis.com/request_count"'
    _install(
        monkeypatch,
        policies={
            "alertPolicies": [
                _threshold_policy(policy_id="1", condition_filter=shared),
                _threshold_policy(policy_id="2", condition_filter=shared),
                _threshold_policy(policy_id="3", condition_filter=other),
            ]
        },
    )

    # Act.
    result = _call(action="policies")

    # Assert: deduplicated, first-seen order, on the response itself.
    assert result["condition_filters"] == [shared, other]
