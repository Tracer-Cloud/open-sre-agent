"""GCP service tools: Cloud Run, Cloud SQL, Pub/Sub backlog, Error Reporting."""

from __future__ import annotations

from typing import Any

import pytest

from integrations.gcp.tools.gcp_error_reporting_tool import gcp_error_reporting_top_errors
from integrations.gcp.tools.gcp_error_reporting_tool.groups import (
    MESSAGE_BUDGET,
    normalize_group,
    truncate_message,
)
from integrations.gcp.tools.gcp_list_cloud_run_services_tool import gcp_list_cloud_run_services
from integrations.gcp.tools.gcp_list_cloud_run_services_tool.services import (
    normalize_service,
    service_location,
)
from integrations.gcp.tools.gcp_list_cloud_sql_instances_tool import gcp_list_cloud_sql_instances
from integrations.gcp.tools.gcp_list_cloud_sql_instances_tool.instances import (
    disk_usage,
    normalize_instance,
)
from integrations.gcp.tools.gcp_pubsub_backlog_tool import gcp_pubsub_backlog
from integrations.gcp.tools.gcp_pubsub_backlog_tool.backlog import (
    attach_backlog,
    latest_by_subscription,
    normalize_subscription,
)
from tools.registry import get_registered_tool

_ONE_CREDENTIAL = {"project_id": "acme"}

_GB = 1024**3


# --- registry ----------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "gcp_list_cloud_run_services",
        "gcp_list_cloud_sql_instances",
        "gcp_pubsub_backlog",
        "gcp_error_reporting_top_errors",
    ],
)
def test_tool_is_registered(name: str) -> None:
    registered = get_registered_tool(name)

    assert registered is not None
    assert registered.source == "gcp"
    # Injected params override model input, which would make `project` inert —
    # the bug that left the Kubernetes `context` parameter unusable.
    assert "project" not in (registered.injected_params or ())


# --- Cloud Run normalization -------------------------------------------------


def _service(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "projects/acme/locations/us-central1/services/checkout",
        "uri": "https://checkout-abc.a.run.app",
        "ingress": "INGRESS_TRAFFIC_ALL",
        "updateTime": "2026-08-05T11:00:00Z",
        "lastModifier": "deployer@acme.iam.gserviceaccount.com",
        "latestCreatedRevision": "projects/acme/locations/us-central1/revisions/checkout-00007",
        "latestReadyRevision": "projects/acme/locations/us-central1/revisions/checkout-00007",
        "terminalCondition": {"type": "Ready", "state": "CONDITION_SUCCEEDED"},
        "template": {"containers": [{"image": "gcr.io/acme/checkout:1.4.0"}]},
    }
    base.update(overrides)
    return base


def test_service_location_reads_the_region_out_of_the_resource_name() -> None:
    assert service_location("projects/acme/locations/europe-west1/services/api") == "europe-west1"


def test_service_location_tolerates_a_short_name() -> None:
    assert service_location("checkout") == ""


def test_normalize_service_lifts_the_useful_fields() -> None:
    normalized = normalize_service(_service(), "acme")

    assert normalized["name"] == "checkout"
    assert normalized["location"] == "us-central1"
    assert normalized["ready"] is True
    assert normalized["rollout_pending"] is False
    assert normalized["images"] == ["gcr.io/acme/checkout:1.4.0"]
    assert normalized["url"] == "https://checkout-abc.a.run.app"


def test_normalize_service_flags_a_revision_that_never_took_traffic() -> None:
    # The failure this tool exists for: the deploy "succeeded", the service is
    # still Ready, and the old revision is serving every request.
    normalized = normalize_service(
        _service(
            latestCreatedRevision="projects/acme/locations/us-central1/revisions/checkout-00008"
        ),
        "acme",
    )

    assert normalized["ready"] is True
    assert normalized["rollout_pending"] is True
    assert normalized["latest_created_revision"] == "checkout-00008"
    assert normalized["latest_ready_revision"] == "checkout-00007"


def test_normalize_service_renders_the_reason_a_revision_failed() -> None:
    normalized = normalize_service(
        _service(
            terminalCondition={
                "type": "Ready",
                "state": "CONDITION_FAILED",
                "reason": "RevisionFailed",
                "message": "Image not found",
            }
        ),
        "acme",
    )

    assert normalized["ready"] is False
    assert normalized["failing_conditions"] == ["Ready — RevisionFailed: Image not found"]


def test_normalize_service_skips_conditions_that_are_satisfied() -> None:
    normalized = normalize_service(
        _service(
            conditions=[
                {"type": "RoutesReady", "state": "CONDITION_SUCCEEDED"},
                {"type": "ConfigurationsReady", "state": "CONDITION_FAILED", "reason": "Quota"},
            ]
        ),
        "acme",
    )

    assert normalized["failing_conditions"] == ["ConfigurationsReady — Quota"]


def test_normalize_service_reports_live_traffic_not_requested_traffic() -> None:
    normalized = normalize_service(
        _service(
            traffic=[{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", "percent": 100}],
            trafficStatuses=[
                {
                    "revision": "projects/acme/locations/us-central1/revisions/checkout-00007",
                    "percent": 100,
                }
            ],
        ),
        "acme",
    )

    assert normalized["traffic"] == [{"revision": "checkout-00007", "percent": 100}]


def test_normalize_service_omits_optional_keys_when_absent() -> None:
    bare = normalize_service({"name": "projects/acme/locations/us-central1/services/x"}, "acme")

    assert "images" not in bare
    assert "traffic" not in bare
    assert "failing_conditions" not in bare
    assert bare["ready"] is False


# --- Cloud Run tool ----------------------------------------------------------


class _FakeRunServices:
    def __init__(self, calls: list[dict[str, Any]], pages: list[dict[str, Any]]) -> None:
        self._calls = calls
        self._pages = pages

    def list(self, parent: str, pageSize: int) -> _FakeRunServices:  # noqa: N803
        self._calls.append({"parent": parent, "pageSize": pageSize})
        return self

    def execute(self) -> dict[str, Any]:
        return self._pages.pop(0)


class _FakeRunService:
    def __init__(self, calls: list[dict[str, Any]], pages: list[dict[str, Any]]) -> None:
        self._services = _FakeRunServices(calls, pages)

    def projects(self) -> _FakeRunService:
        return self

    def locations(self) -> _FakeRunService:
        return self

    def services(self) -> _FakeRunServices:
        return self._services


class _RaisingRunServices:
    def __init__(self, failing_project: str) -> None:
        self._failing = failing_project
        self._parent = ""

    def list(self, parent: str, pageSize: int) -> _RaisingRunServices:  # noqa: N803, ARG002
        self._parent = parent
        return self

    def execute(self) -> dict[str, Any]:
        if self._parent == f"projects/{self._failing}/locations/-":
            raise RuntimeError("run.services.list denied")
        return {"services": [_service()]}


class _RaisingRunService:
    def __init__(self, failing_project: str) -> None:
        self._services = _RaisingRunServices(failing_project)

    def projects(self) -> _RaisingRunService:
        return self

    def locations(self) -> _RaisingRunService:
        return self

    def services(self) -> _RaisingRunServices:
        return self._services


def _install_run(monkeypatch: pytest.MonkeyPatch, service: Any) -> None:
    import integrations.gcp.tools.gcp_list_cloud_run_services_tool as module

    def _build(_config: Any, _api: tuple[str, str]) -> Any:
        return service

    monkeypatch.setattr(module, "build_service", _build)


def test_run_tool_asks_for_every_region_in_one_call(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    _install_run(monkeypatch, _FakeRunService(calls, [{"services": []}]))

    gcp_list_cloud_run_services(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    # locations/- is what saves the agent from guessing a region.
    assert calls[0]["parent"] == "projects/acme/locations/-"


def test_run_tool_matches_a_name_substring(monkeypatch: pytest.MonkeyPatch) -> None:
    page = {
        "services": [
            _service(),
            _service(name="projects/acme/locations/us-central1/services/billing"),
        ]
    }
    _install_run(monkeypatch, _FakeRunService([], [page]))

    result = gcp_list_cloud_run_services(
        name_contains="BILL",
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert [item["name"] for item in result["services"]] == ["billing"]


def test_run_tool_explains_a_stuck_rollout(monkeypatch: pytest.MonkeyPatch) -> None:
    stuck = _service(
        latestCreatedRevision="projects/acme/locations/us-central1/revisions/checkout-00008"
    )
    _install_run(monkeypatch, _FakeRunService([], [{"services": [stuck]}]))

    result = gcp_list_cloud_run_services(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert "older revision" in result["note"]


def test_run_tool_unhealthy_only_keeps_a_ready_service_with_a_stuck_rollout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stuck = _service(
        name="projects/acme/locations/us-central1/services/stuck",
        latestCreatedRevision="projects/acme/locations/us-central1/revisions/stuck-00008",
        latestReadyRevision="projects/acme/locations/us-central1/revisions/stuck-00007",
    )
    _install_run(monkeypatch, _FakeRunService([], [{"services": [_service(), stuck]}]))

    result = gcp_list_cloud_run_services(
        unhealthy_only=True,
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert [item["name"] for item in result["services"]] == ["stuck"]


def test_run_tool_surfaces_regions_it_could_not_reach(monkeypatch: pytest.MonkeyPatch) -> None:
    page = {"services": [], "unreachable": ["asia-south2"]}
    _install_run(monkeypatch, _FakeRunService([], [page]))

    result = gcp_list_cloud_run_services(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert result["unreachable_regions"] == ["asia-south2"]


def test_run_tool_keeps_the_projects_that_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_run(monkeypatch, _RaisingRunService("locked"))

    result = gcp_list_cloud_run_services(
        project="*",
        default_project="acme",
        available_projects=["acme", "locked"],
        project_configs={"acme": _ONE_CREDENTIAL, "locked": _ONE_CREDENTIAL},
    )

    assert result["found"] is True
    assert result["service_count"] == 1
    assert result["partial_errors"] == ["locked: RuntimeError calling the Google API"]


def test_run_tool_fails_when_no_project_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_run(monkeypatch, _RaisingRunService("acme"))

    result = gcp_list_cloud_run_services(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert result["found"] is False
    assert result["services"] == []


# --- Cloud SQL normalization -------------------------------------------------


def _instance(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "orders-prod",
        "state": "RUNNABLE",
        "databaseVersion": "POSTGRES_15",
        "databaseInstalledVersion": "POSTGRES_15_6",
        "region": "us-central1",
        "gceZone": "us-central1-a",
        "instanceType": "CLOUD_SQL_INSTANCE",
        "connectionName": "acme:us-central1:orders-prod",
        "currentDiskSize": str(40 * _GB),
        "settings": {
            "tier": "db-custom-4-16384",
            "availabilityType": "REGIONAL",
            "dataDiskSizeGb": "100",
            "backupConfiguration": {"enabled": True},
        },
    }
    base.update(overrides)
    return base


def test_disk_usage_converts_bytes_against_gigabytes() -> None:
    # currentDiskSize is bytes and dataDiskSizeGb is GB; mixing them is the
    # obvious way to get this wrong.
    usage = disk_usage({"currentDiskSize": str(50 * _GB)}, {"dataDiskSizeGb": "100"})

    assert usage == {"disk_size_gb": 100, "disk_used_gb": 50.0, "disk_used_ratio": 0.5}


def test_disk_usage_is_empty_when_either_side_is_missing() -> None:
    assert disk_usage({}, {"dataDiskSizeGb": "100"}) == {}
    assert disk_usage({"currentDiskSize": "1"}, {}) == {}


def test_normalize_instance_lifts_the_useful_fields() -> None:
    normalized = normalize_instance(_instance(), "acme")

    assert normalized["state"] == "RUNNABLE"
    assert normalized["healthy"] is True
    assert normalized["database_version"] == "POSTGRES_15_6"
    assert normalized["availability_type"] == "REGIONAL"
    assert normalized["accepts_writes"] is True
    assert normalized["backups_enabled"] is True


def test_normalize_instance_is_unhealthy_under_disk_pressure_while_runnable() -> None:
    # Cloud SQL keeps reporting RUNNABLE while refusing writes for want of disk,
    # so the state field alone never shows this.
    normalized = normalize_instance(_instance(currentDiskSize=str(95 * _GB)), "acme")

    assert normalized["state"] == "RUNNABLE"
    assert normalized["disk_pressure"] is True
    assert normalized["healthy"] is False


def test_normalize_instance_identifies_a_read_replica() -> None:
    normalized = normalize_instance(
        _instance(
            name="orders-replica",
            instanceType="READ_REPLICA_INSTANCE",
            masterInstanceName="acme:orders-prod",
        ),
        "acme",
    )

    assert normalized["accepts_writes"] is False
    assert normalized["replica_of"] == "orders-prod"


def test_normalize_instance_reports_a_suspension_reason() -> None:
    normalized = normalize_instance(
        _instance(state="SUSPENDED", suspensionReason=["BILLING_ISSUE"]), "acme"
    )

    assert normalized["healthy"] is False
    assert normalized["suspension_reasons"] == ["BILLING_ISSUE"]


def test_normalize_instance_reports_scheduled_maintenance() -> None:
    normalized = normalize_instance(
        _instance(scheduledMaintenance={"startTime": "2026-08-06T02:00:00Z", "canDefer": True}),
        "acme",
    )

    assert normalized["scheduled_maintenance"]["starts_at"] == "2026-08-06T02:00:00Z"
    assert normalized["scheduled_maintenance"]["can_defer"] is True


def test_normalize_instance_omits_replica_keys_for_a_standalone_primary() -> None:
    normalized = normalize_instance(_instance(), "acme")

    assert "replica_of" not in normalized
    assert "replicas" not in normalized
    assert "disk_pressure" not in normalized


# --- Cloud SQL tool ----------------------------------------------------------


class _FakeSqlInstances:
    def __init__(self, calls: list[dict[str, Any]], pages: list[dict[str, Any]]) -> None:
        self._calls = calls
        self._pages = pages

    def list(
        self,
        project: str,
        maxResults: int,  # noqa: N803
        filter: str | None,  # noqa: A002
    ) -> _FakeSqlInstances:
        self._calls.append({"project": project, "maxResults": maxResults, "filter": filter})
        return self

    def execute(self) -> dict[str, Any]:
        return self._pages.pop(0)


class _FakeSqlService:
    def __init__(self, calls: list[dict[str, Any]], pages: list[dict[str, Any]]) -> None:
        self._instances = _FakeSqlInstances(calls, pages)

    def instances(self) -> _FakeSqlInstances:
        return self._instances


class _RaisingSqlInstances:
    def __init__(self, failing_project: str) -> None:
        self._failing = failing_project
        self._project = ""

    def list(
        self,
        project: str,
        maxResults: int,  # noqa: N803, ARG002
        filter: str | None,  # noqa: A002, ARG002
    ) -> _RaisingSqlInstances:
        self._project = project
        return self

    def execute(self) -> dict[str, Any]:
        if self._project == self._failing:
            raise RuntimeError("sqladmin.instances.list denied")
        return {"items": [_instance()]}


class _RaisingSqlService:
    def __init__(self, failing_project: str) -> None:
        self._instances = _RaisingSqlInstances(failing_project)

    def instances(self) -> _RaisingSqlInstances:
        return self._instances


def _install_sql(monkeypatch: pytest.MonkeyPatch, service: Any) -> None:
    import integrations.gcp.tools.gcp_list_cloud_sql_instances_tool as module

    def _build(_config: Any, _api: tuple[str, str]) -> Any:
        return service

    monkeypatch.setattr(module, "build_service", _build)


def test_sql_tool_rejects_an_unknown_state() -> None:
    result = gcp_list_cloud_sql_instances(
        state="ON_FIRE", default_project="acme", available_projects=["acme"]
    )

    assert result["available"] is False
    assert "ON_FIRE" in result["error"]


def test_sql_tool_pushes_the_state_filter_to_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    _install_sql(monkeypatch, _FakeSqlService(calls, [{"items": []}]))

    gcp_list_cloud_sql_instances(
        state="suspended",
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert calls[0]["filter"] == "state:SUSPENDED"


def test_sql_tool_sends_no_filter_when_unfiltered(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    _install_sql(monkeypatch, _FakeSqlService(calls, [{"items": []}]))

    gcp_list_cloud_sql_instances(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert calls[0]["filter"] is None


def test_sql_tool_matches_a_name_substring_client_side(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Cloud SQL's filter language has no substring operator, so name matching
    # must not reach the API.
    calls: list[dict[str, Any]] = []
    page = {"items": [_instance(), _instance(name="reporting-prod")]}
    _install_sql(monkeypatch, _FakeSqlService(calls, [page]))

    result = gcp_list_cloud_sql_instances(
        name_contains="REPORT",
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert calls[0]["filter"] is None
    assert [item["name"] for item in result["instances"]] == ["reporting-prod"]


def test_sql_tool_notes_an_instance_near_its_disk_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = {"items": [_instance(currentDiskSize=str(95 * _GB))]}
    _install_sql(monkeypatch, _FakeSqlService([], [page]))

    result = gcp_list_cloud_sql_instances(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert "disk" in result["note"]


def test_sql_tool_unhealthy_only_keeps_a_runnable_instance_out_of_disk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    full = _instance(name="full-prod", currentDiskSize=str(95 * _GB))
    _install_sql(monkeypatch, _FakeSqlService([], [{"items": [_instance(), full]}]))

    result = gcp_list_cloud_sql_instances(
        unhealthy_only=True,
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert [item["name"] for item in result["instances"]] == ["full-prod"]


def test_sql_tool_keeps_the_projects_that_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_sql(monkeypatch, _RaisingSqlService("locked"))

    result = gcp_list_cloud_sql_instances(
        project="*",
        default_project="acme",
        available_projects=["acme", "locked"],
        project_configs={"acme": _ONE_CREDENTIAL, "locked": _ONE_CREDENTIAL},
    )

    assert result["found"] is True
    assert result["instance_count"] == 1
    assert result["partial_errors"] == ["locked: RuntimeError calling the Google API"]


def test_sql_tool_fails_when_no_project_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_sql(monkeypatch, _RaisingSqlService("acme"))

    result = gcp_list_cloud_sql_instances(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert result["found"] is False
    assert result["instances"] == []


# --- Pub/Sub normalization and the monitoring join ---------------------------


def _subscription(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "projects/acme/subscriptions/orders-worker",
        "topic": "projects/acme/topics/orders",
        "ackDeadlineSeconds": 60,
        "messageRetentionDuration": "604800s",
    }
    base.update(overrides)
    return base


def _series(subscription: str, value: Any, key: str = "int64Value") -> dict[str, Any]:
    return {
        "resource": {"labels": {"subscription_id": subscription}},
        "points": [{"value": {key: value}}],
    }


def test_latest_by_subscription_keys_on_the_resource_label() -> None:
    latest = latest_by_subscription([_series("orders-worker", "42")])

    assert latest == {"orders-worker": 42}


def test_latest_by_subscription_takes_the_newest_point() -> None:
    # Cloud Monitoring returns points newest-first.
    series = {
        "resource": {"labels": {"subscription_id": "orders-worker"}},
        "points": [{"value": {"int64Value": "9"}}, {"value": {"int64Value": "100"}}],
    }

    assert latest_by_subscription([series]) == {"orders-worker": 9}


def test_latest_by_subscription_skips_series_it_cannot_key() -> None:
    assert latest_by_subscription([{"points": [{"value": {"int64Value": "1"}}]}, "junk"]) == {}


def test_normalize_subscription_reports_a_pull_subscription() -> None:
    normalized = normalize_subscription(_subscription(), "acme")

    assert normalized["name"] == "orders-worker"
    assert normalized["topic"] == "orders"
    assert normalized["delivery"] == "pull"
    assert normalized["message_retention_seconds"] == 604800.0
    assert "push_endpoint" not in normalized


def test_normalize_subscription_reports_a_push_endpoint() -> None:
    normalized = normalize_subscription(
        _subscription(pushConfig={"pushEndpoint": "https://worker.acme.dev/events"}), "acme"
    )

    assert normalized["delivery"] == "push"
    assert normalized["push_endpoint"] == "https://worker.acme.dev/events"


def test_normalize_subscription_flags_a_retry_policy_with_no_dead_letter_topic() -> None:
    # No dead-letter topic means a poison message is retried forever, which is a
    # common reason a backlog never drains.
    normalized = normalize_subscription(
        _subscription(retryPolicy={"minimumBackoff": "10s"}), "acme"
    )

    assert normalized["dead_letter_topic"] == ""
    assert normalized["retry_min_backoff_seconds"] == 10.0


def test_normalize_subscription_reports_a_dead_letter_policy() -> None:
    normalized = normalize_subscription(
        _subscription(
            deadLetterPolicy={
                "deadLetterTopic": "projects/acme/topics/orders-dlq",
                "maxDeliveryAttempts": 5,
            }
        ),
        "acme",
    )

    assert normalized["dead_letter_topic"] == "orders-dlq"
    assert normalized["max_delivery_attempts"] == 5


def test_normalize_subscription_flags_a_detached_subscription() -> None:
    assert normalize_subscription(_subscription(detached=True), "acme")["detached"] is True


def test_attach_backlog_marks_a_stalled_subscription() -> None:
    entry = attach_backlog(
        {"name": "orders-worker"}, {"orders-worker": 1200}, {"orders-worker": 900.0}
    )

    assert entry["undelivered_messages"] == 1200
    assert entry["oldest_unacked_age_seconds"] == 900.0
    assert entry["stalled"] is True


def test_attach_backlog_leaves_a_fresh_subscription_unstalled() -> None:
    entry = attach_backlog({"name": "orders-worker"}, {"orders-worker": 3}, {"orders-worker": 2.0})

    assert "stalled" not in entry
    assert "backlog_unknown" not in entry


def test_attach_backlog_distinguishes_no_backlog_from_no_metric() -> None:
    # An unreadable metric would otherwise render as a healthy zero.
    entry = attach_backlog({"name": "orders-worker"}, {}, {})

    assert entry["backlog_unknown"] is True
    assert "undelivered_messages" not in entry


# --- Pub/Sub tool ------------------------------------------------------------


class _FakeSubscriptions:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self._pages = pages

    def list(self, project: str, pageSize: int) -> _FakeSubscriptions:  # noqa: N803, ARG002
        return self

    def execute(self) -> dict[str, Any]:
        return self._pages.pop(0) if self._pages else {"subscriptions": []}


class _FakePubsubService:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self._subscriptions = _FakeSubscriptions(pages)

    def projects(self) -> _FakePubsubService:
        return self

    def subscriptions(self) -> _FakeSubscriptions:
        return self._subscriptions


class _FakeTimeSeries:
    def __init__(self, by_metric: dict[str, list[dict[str, Any]]], fail: bool) -> None:
        self._by_metric = by_metric
        self._fail = fail
        self._metric = ""

    def list(self, **kwargs: Any) -> _FakeTimeSeries:
        self._metric = str(kwargs.get("filter", ""))
        return self

    def execute(self) -> dict[str, Any]:
        if self._fail:
            raise RuntimeError("monitoring.timeSeries.list denied")
        for metric, series in self._by_metric.items():
            if metric in self._metric:
                return {"timeSeries": series}
        return {"timeSeries": []}


class _FakeMonitoringService:
    def __init__(self, by_metric: dict[str, list[dict[str, Any]]], fail: bool = False) -> None:
        self._series = _FakeTimeSeries(by_metric, fail)

    def projects(self) -> _FakeMonitoringService:
        return self

    def timeSeries(self) -> _FakeTimeSeries:  # noqa: N802 — matches the API method name
        return self._series


def _install_pubsub(monkeypatch: pytest.MonkeyPatch, pubsub: Any, monitoring: Any) -> None:
    import integrations.gcp.tools.gcp_pubsub_backlog_tool as module

    def _build(_config: Any, api: tuple[str, str]) -> Any:
        return monitoring if api[0] == "monitoring" else pubsub

    monkeypatch.setattr(module, "build_service", _build)


def test_pubsub_tool_joins_the_backlog_metrics_onto_the_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_pubsub(
        monkeypatch,
        _FakePubsubService([{"subscriptions": [_subscription()]}]),
        _FakeMonitoringService(
            {
                "num_undelivered_messages": [_series("orders-worker", "1200")],
                "oldest_unacked_message_age": [_series("orders-worker", 900.0, key="doubleValue")],
            }
        ),
    )

    result = gcp_pubsub_backlog(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    entry = result["subscriptions"][0]
    assert entry["undelivered_messages"] == 1200
    assert entry["oldest_unacked_age_seconds"] == 900.0
    assert result["stalled_count"] == 1
    assert result["total_undelivered"] == 1200
    assert "consumer" in result["note"]


def test_pubsub_tool_sorts_the_deepest_backlog_first(monkeypatch: pytest.MonkeyPatch) -> None:
    page = {
        "subscriptions": [
            _subscription(),
            _subscription(name="projects/acme/subscriptions/emails-worker"),
        ]
    }
    _install_pubsub(
        monkeypatch,
        _FakePubsubService([page]),
        _FakeMonitoringService(
            {
                "num_undelivered_messages": [
                    _series("orders-worker", "5"),
                    _series("emails-worker", "5000"),
                ]
            }
        ),
    )

    result = gcp_pubsub_backlog(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert [item["name"] for item in result["subscriptions"]] == [
        "emails-worker",
        "orders-worker",
    ]


def test_pubsub_tool_still_reports_config_when_monitoring_is_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # roles/pubsub.viewer without roles/monitoring.viewer: the configuration is
    # still worth having, and a missing metric must not read as a zero backlog.
    _install_pubsub(
        monkeypatch,
        _FakePubsubService([{"subscriptions": [_subscription()]}]),
        _FakeMonitoringService({}, fail=True),
    )

    result = gcp_pubsub_backlog(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert result["found"] is True
    assert result["subscriptions"][0]["backlog_unknown"] is True
    assert "monitoring.viewer" in result["note"]


def test_pubsub_tool_backlogged_only_drops_a_drained_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = {
        "subscriptions": [
            _subscription(),
            _subscription(name="projects/acme/subscriptions/emails-worker"),
        ]
    }
    _install_pubsub(
        monkeypatch,
        _FakePubsubService([page]),
        _FakeMonitoringService(
            {
                "num_undelivered_messages": [
                    _series("orders-worker", "0"),
                    _series("emails-worker", "7"),
                ]
            }
        ),
    )

    result = gcp_pubsub_backlog(
        backlogged_only=True,
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert [item["name"] for item in result["subscriptions"]] == ["emails-worker"]


def test_pubsub_tool_matches_a_topic_substring(monkeypatch: pytest.MonkeyPatch) -> None:
    page = {
        "subscriptions": [
            _subscription(),
            _subscription(
                name="projects/acme/subscriptions/emails-worker",
                topic="projects/acme/topics/emails",
            ),
        ]
    }
    _install_pubsub(monkeypatch, _FakePubsubService([page]), _FakeMonitoringService({}))

    result = gcp_pubsub_backlog(
        name_contains="EMAILS",
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert [item["name"] for item in result["subscriptions"]] == ["emails-worker"]


def test_pubsub_tool_fails_when_the_subscription_listing_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RaisingSubscriptions:
        def list(self, project: str, pageSize: int) -> Any:  # noqa: N803, ARG002
            return self

        def execute(self) -> dict[str, Any]:
            raise RuntimeError("pubsub.subscriptions.list denied")

    class _RaisingPubsubService:
        def projects(self) -> Any:
            return self

        def subscriptions(self) -> Any:
            return _RaisingSubscriptions()

    _install_pubsub(monkeypatch, _RaisingPubsubService(), _FakeMonitoringService({}))

    result = gcp_pubsub_backlog(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert result["found"] is False
    assert result["subscriptions"] == []


# --- Error Reporting normalization -------------------------------------------


def _group_stats(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "group": {"groupId": "CKjF2Yz", "resolutionStatus": "OPEN"},
        "count": "412",
        "affectedUsersCount": "37",
        "firstSeenTime": "2026-08-05T11:40:00Z",
        "lastSeenTime": "2026-08-05T11:59:00Z",
        "representative": {
            "message": "ValueError: bad checkout id\n  at checkout.py:88",
            "serviceContext": {"service": "checkout", "version": "1.4.0"},
        },
    }
    base.update(overrides)
    return base


def test_truncate_message_keeps_the_leading_frames() -> None:
    truncated = truncate_message("x" * (MESSAGE_BUDGET + 400))

    assert truncated.startswith("x" * 100)
    assert truncated.endswith("…[truncated]")
    assert len(truncated) < MESSAGE_BUDGET + 400


def test_truncate_message_leaves_a_short_trace_alone() -> None:
    assert truncate_message("  ValueError: nope  ") == "ValueError: nope"


def test_normalize_group_lifts_the_useful_fields() -> None:
    normalized = normalize_group(_group_stats(), "acme")

    assert normalized["group_id"] == "CKjF2Yz"
    assert normalized["count"] == 412
    assert normalized["affected_users"] == 37
    # The field that separates a regression from long-standing noise.
    assert normalized["first_seen"] == "2026-08-05T11:40:00Z"
    assert normalized["service"] == "checkout"
    assert normalized["resolution_status"] == "OPEN"


def test_normalize_group_reports_several_services_only_when_shared() -> None:
    shared = normalize_group(
        _group_stats(
            affectedServices=[
                {"service": "checkout", "version": "1.4.0"},
                {"service": "billing", "version": "2.0.1"},
            ]
        ),
        "acme",
    )
    single = normalize_group(
        _group_stats(affectedServices=[{"service": "checkout", "version": "1.4.0"}]), "acme"
    )

    assert shared["affected_services"] == ["checkout@1.4.0", "billing@2.0.1"]
    assert "affected_services" not in single


def test_normalize_group_returns_tracking_issues() -> None:
    normalized = normalize_group(
        _group_stats(group={"groupId": "g1", "trackingIssues": [{"url": "https://x/issues/9"}]}),
        "acme",
    )

    assert normalized["tracking_issues"] == ["https://x/issues/9"]


def test_normalize_group_parses_string_encoded_counts() -> None:
    assert normalize_group(_group_stats(count=None, affectedUsersCount="oops"), "acme") == {
        **normalize_group(_group_stats(), "acme"),
        "count": 0,
        "affected_users": 0,
    }


# --- Error Reporting tool ----------------------------------------------------


class _FakeGroupStats:
    # The signature is spelled out rather than taking **kwargs on purpose.
    # googleapiclient renames dotted REST query params with an underscore, so a
    # permissive fake happily accepts `timeRange.period` and the tool only fails
    # against the real API. Keeping the names exact makes that a test failure.
    def __init__(self, calls: list[dict[str, Any]], pages: list[dict[str, Any]]) -> None:
        self._calls = calls
        self._pages = pages

    def list(
        self,
        projectName: str,  # noqa: N803
        pageSize: int,  # noqa: N803
        order: str,
        timeRange_period: str,  # noqa: N803
        serviceFilter_service: str | None,  # noqa: N803
    ) -> _FakeGroupStats:
        self._calls.append(
            {
                "projectName": projectName,
                "pageSize": pageSize,
                "order": order,
                "timeRange_period": timeRange_period,
                "serviceFilter_service": serviceFilter_service,
            }
        )
        return self

    def execute(self) -> dict[str, Any]:
        return self._pages.pop(0) if self._pages else {"errorGroupStats": []}


class _FakeErrorReportingService:
    def __init__(self, calls: list[dict[str, Any]], pages: list[dict[str, Any]]) -> None:
        self._stats = _FakeGroupStats(calls, pages)

    def projects(self) -> _FakeErrorReportingService:
        return self

    def groupStats(self) -> _FakeGroupStats:  # noqa: N802 — matches the API method name
        return self._stats


class _RaisingGroupStats:
    def __init__(self, failing_project: str) -> None:
        self._failing = failing_project
        self._project = ""

    def list(
        self,
        projectName: str,  # noqa: N803
        pageSize: int,  # noqa: N803, ARG002
        order: str,  # noqa: ARG002
        timeRange_period: str,  # noqa: N803, ARG002
        serviceFilter_service: str | None,  # noqa: N803, ARG002
    ) -> _RaisingGroupStats:
        self._project = projectName
        return self

    def execute(self) -> dict[str, Any]:
        if self._project == f"projects/{self._failing}":
            raise RuntimeError("clouderrorreporting.groupStats.list denied")
        return {"errorGroupStats": [_group_stats()]}


class _RaisingErrorReportingService:
    def __init__(self, failing_project: str) -> None:
        self._stats = _RaisingGroupStats(failing_project)

    def projects(self) -> _RaisingErrorReportingService:
        return self

    def groupStats(self) -> _RaisingGroupStats:  # noqa: N802 — matches the API method name
        return self._stats


def _install_error_reporting(monkeypatch: pytest.MonkeyPatch, service: Any) -> None:
    import integrations.gcp.tools.gcp_error_reporting_tool as module

    def _build(_config: Any, _api: tuple[str, str]) -> Any:
        return service

    monkeypatch.setattr(module, "build_service", _build)


def test_error_reporting_tool_rejects_an_unknown_period() -> None:
    result = gcp_error_reporting_top_errors(
        period="2y", default_project="acme", available_projects=["acme"]
    )

    assert result["available"] is False
    assert "2y" in result["error"]


def test_error_reporting_tool_rejects_an_unknown_order() -> None:
    result = gcp_error_reporting_top_errors(
        order="alphabetical", default_project="acme", available_projects=["acme"]
    )

    assert result["available"] is False
    assert "alphabetical" in result["error"]


def test_error_reporting_tool_maps_friendly_values_to_api_enums(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    _install_error_reporting(monkeypatch, _FakeErrorReportingService(calls, []))

    gcp_error_reporting_top_errors(
        period="6h",
        order="first_seen",
        service="checkout",
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert calls[0]["timeRange_period"] == "PERIOD_6_HOURS"
    assert calls[0]["order"] == "CREATED_DESC"
    assert calls[0]["serviceFilter_service"] == "checkout"


def test_error_reporting_tool_sends_no_service_filter_when_unfiltered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    _install_error_reporting(monkeypatch, _FakeErrorReportingService(calls, []))

    gcp_error_reporting_top_errors(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert calls[0]["serviceFilter_service"] is None


def test_error_reporting_tool_reranks_a_merged_result_on_the_requested_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Each project is ranked independently by the API. Re-ranking on count
    # regardless of `order` would silently return the wrong top-N.
    loud_but_old = _group_stats(
        group={"groupId": "old"}, count="9000", lastSeenTime="2026-08-05T09:00:00Z"
    )
    quiet_but_recent = _group_stats(
        group={"groupId": "new"}, count="3", lastSeenTime="2026-08-05T11:59:00Z"
    )
    _install_error_reporting(
        monkeypatch,
        _FakeErrorReportingService(
            [],
            [
                {"errorGroupStats": [loud_but_old]},
                {"errorGroupStats": [quiet_but_recent]},
            ],
        ),
    )

    result = gcp_error_reporting_top_errors(
        project="*",
        order="last_seen",
        limit=1,
        default_project="acme",
        available_projects=["acme", "research"],
        project_configs={"acme": _ONE_CREDENTIAL, "research": _ONE_CREDENTIAL},
    )

    assert [item["group_id"] for item in result["error_groups"]] == ["new"]


def test_error_reporting_tool_explains_an_empty_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_error_reporting(monkeypatch, _FakeErrorReportingService([], []))

    result = gcp_error_reporting_top_errors(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert result["found"] is False
    assert "gcp_logging_query" in result["note"]


def test_error_reporting_tool_keeps_the_projects_that_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_error_reporting(monkeypatch, _RaisingErrorReportingService("locked"))

    result = gcp_error_reporting_top_errors(
        project="*",
        default_project="acme",
        available_projects=["acme", "locked"],
        project_configs={"acme": _ONE_CREDENTIAL, "locked": _ONE_CREDENTIAL},
    )

    assert result["found"] is True
    assert result["group_count"] == 1
    assert result["total_errors"] == 412
    assert result["partial_errors"] == ["locked: RuntimeError calling the Google API"]


def test_error_reporting_tool_fails_when_no_project_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_error_reporting(monkeypatch, _RaisingErrorReportingService("acme"))

    result = gcp_error_reporting_top_errors(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert result["found"] is False
    assert result["error_groups"] == []
