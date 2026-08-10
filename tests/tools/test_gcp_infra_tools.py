"""GCP infrastructure tools: GKE clusters, Cloud Audit Logs, Compute instances."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from integrations.config_models import KubernetesIntegrationConfig
from integrations.gcp.tools.gcp_audit_log_query_tool import gcp_audit_log_query
from integrations.gcp.tools.gcp_audit_log_query_tool.filters import (
    build_audit_filter,
    normalize_log_type,
    quote,
)
from integrations.gcp.tools.gcp_audit_log_query_tool.records import normalize_record
from integrations.gcp.tools.gcp_list_compute_instances_tool import gcp_list_compute_instances
from integrations.gcp.tools.gcp_list_compute_instances_tool.instances import (
    flatten_aggregated,
    normalize_instance,
)
from integrations.gcp.tools.gcp_list_gke_clusters_tool import gcp_list_gke_clusters
from integrations.gcp.tools.gcp_list_gke_clusters_tool.clusters import (
    kubeconfig_context,
    normalize_cluster,
)
from integrations.gcp.tools.gcp_list_gke_clusters_tool.correlation import annotate
from integrations.gcp.tools.gcp_list_gke_clusters_tool.params import registered_clusters
from tools.registry import get_registered_tool

_NOW = datetime(2026, 8, 5, 12, 0, 0, tzinfo=UTC)

_ONE_CREDENTIAL = {"project_id": "acme"}
_OTHER_CREDENTIAL = {"project_id": "research", "impersonate_service_account": "ro@x"}


# --- registry ----------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["gcp_list_gke_clusters", "gcp_audit_log_query", "gcp_list_compute_instances"]
)
def test_tool_is_registered(name: str) -> None:
    registered = get_registered_tool(name)

    assert registered is not None
    assert registered.source == "gcp"
    # Injected params override model input, which would make `project` inert —
    # the bug that left the Kubernetes `context` parameter unusable.
    assert "project" not in (registered.injected_params or ())


# --- GKE cluster normalization -----------------------------------------------


def _cluster(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "prod",
        "location": "us-central1",
        "status": "RUNNING",
        "currentMasterVersion": "1.29.4-gke.1043002",
        "currentNodeVersion": "1.29.4-gke.1043002",
        "currentNodeCount": 6,
    }
    base.update(overrides)
    return base


def test_kubeconfig_context_matches_what_gcloud_writes() -> None:
    assert kubeconfig_context("acme", "us-central1", "prod") == "gke_acme_us-central1_prod"


def test_normalize_cluster_lifts_the_useful_fields() -> None:
    normalized = normalize_cluster(_cluster(), "acme")

    assert normalized["name"] == "prod"
    assert normalized["healthy"] is True
    assert normalized["node_count"] == 6
    assert normalized["kubeconfig_context"] == "gke_acme_us-central1_prod"


def test_normalize_cluster_falls_back_to_the_deprecated_zone_field() -> None:
    raw = _cluster(location=None, zone="us-central1-a")
    del raw["location"]

    normalized = normalize_cluster(raw, "acme")

    assert normalized["location"] == "us-central1-a"


def test_normalize_cluster_is_unhealthy_when_a_condition_is_present() -> None:
    normalized = normalize_cluster(
        _cluster(conditions=[{"canonicalCode": "RESOURCE_EXHAUSTED", "message": "quota"}]),
        "acme",
    )

    # RUNNING with a condition still means something is wrong.
    assert normalized["healthy"] is False
    assert normalized["conditions"] == ["RESOURCE_EXHAUSTED: quota"]


def test_normalize_cluster_renders_node_pool_autoscaling() -> None:
    normalized = normalize_cluster(
        _cluster(
            nodePools=[
                {
                    "name": "default-pool",
                    "status": "RUNNING",
                    "initialNodeCount": 2,
                    "version": "1.29.4",
                    "config": {"machineType": "e2-standard-4"},
                    "autoscaling": {"enabled": True, "minNodeCount": 1, "maxNodeCount": 10},
                }
            ]
        ),
        "acme",
    )

    pool = normalized["node_pools"][0]
    assert pool["machine_type"] == "e2-standard-4"
    assert pool["autoscaling"] == "1-10"


def test_normalize_cluster_flags_private_nodes() -> None:
    normalized = normalize_cluster(
        _cluster(privateClusterConfig={"enablePrivateNodes": True, "enablePrivateEndpoint": True}),
        "acme",
    )

    assert normalized["private_nodes"] is True
    assert normalized["private_endpoint_only"] is True


def test_normalize_cluster_omits_private_keys_for_a_public_cluster() -> None:
    assert "private_nodes" not in normalize_cluster(_cluster(), "acme")


# --- GKE correlation ---------------------------------------------------------


def test_annotate_matches_on_the_gcloud_context() -> None:
    clusters = [normalize_cluster(_cluster(), "acme")]

    unmatched = annotate(clusters, [{"name": "gke-prod", "context": "gke_acme_us-central1_prod"}])

    assert clusters[0]["registered_as"] == "gke-prod"
    assert unmatched == []


def test_annotate_matches_on_the_instance_name() -> None:
    clusters = [normalize_cluster(_cluster(), "acme")]

    annotate(clusters, [{"name": "prod", "context": ""}])

    assert clusters[0]["registered_as"] == "prod"


def test_annotate_does_not_match_a_similar_name() -> None:
    clusters = [normalize_cluster(_cluster(), "acme")]

    # A prefix match would silently point the agent at the wrong cluster.
    unmatched = annotate(clusters, [{"name": "prod-eu", "context": "gke_other_eu-west1_prod-eu"}])

    assert "registered_as" not in clusters[0]
    assert unmatched == ["prod"]


def test_annotate_ignores_instances_without_a_name() -> None:
    clusters = [normalize_cluster(_cluster(), "acme")]

    assert annotate(clusters, [{"name": "  ", "context": "gke_acme_us-central1_prod"}]) == ["prod"]


def test_registered_clusters_carries_no_credentials() -> None:
    sources = {
        "kubernetes": {"kubeconfig": "SECRET", "context": "gke_acme_us-central1_prod"},
    }

    entries = registered_clusters(sources)

    assert entries == [{"name": "default", "context": "gke_acme_us-central1_prod"}]
    assert "SECRET" not in str(entries)


def test_registered_clusters_reads_the_context_of_a_named_instance() -> None:
    """A named instance carries its config as a model, not a dict.

    The test above passes the flat single-instance shape, which is the *only*
    shape that reaches this function as a plain dict. Declare a cluster under any
    name but ``default`` — which is what ``KUBERNETES_INSTANCES`` and every
    auto-registered GKE cluster do — and ``classify_integrations`` publishes
    ``_all_kubernetes_instances`` with a ``KubernetesIntegrationConfig`` inside.

    Reading ``context`` off that used to yield ``""``, which silently disabled
    the context-matching half of ``annotate``: a cluster registered under a name
    that differs from the GKE cluster name got reported to the agent as
    unregistered, so it never tried the ``kubernetes_*`` tools that would in fact
    have reached it.
    """
    sources = {
        "kubernetes": KubernetesIntegrationConfig(
            kubeconfig_path="/etc/kube/config", context="gke_acme_us-central1_prod"
        ),
        "_all_kubernetes_instances": [
            {
                "name": "utility",
                "tags": {"env": "utility"},
                "config": KubernetesIntegrationConfig(
                    kubeconfig_path="/etc/kube/config", context="gke_acme_us-central1_prod"
                ),
                "integration_id": "env-kubernetes",
            }
        ],
    }

    entries = registered_clusters(sources)

    assert entries == [{"name": "utility", "context": "gke_acme_us-central1_prod"}]


def test_a_named_instance_matches_the_cluster_it_points_at() -> None:
    """End to end: the instance name and the cluster name deliberately differ.

    Only the kubeconfig context ties them together, so this fails outright if
    ``registered_clusters`` drops the context.
    """
    clusters = [normalize_cluster(_cluster(), "acme")]
    sources = {
        "_all_kubernetes_instances": [
            {
                "name": "utility",
                "tags": {},
                "config": KubernetesIntegrationConfig(
                    kubeconfig_path="/etc/kube/config", context="gke_acme_us-central1_prod"
                ),
                "integration_id": "env-kubernetes",
            }
        ],
    }

    assert annotate(clusters, registered_clusters(sources)) == []
    assert clusters[0]["registered_as"] == "utility"


# --- GKE tool ----------------------------------------------------------------


class _FakeClusters:
    def __init__(self, parents: list[str], pages: list[dict[str, Any]]) -> None:
        self._parents = parents
        self._pages = pages

    def list(self, parent: str) -> _FakeClusters:
        self._parents.append(parent)
        return self

    def execute(self) -> dict[str, Any]:
        return self._pages.pop(0)


class _FakeLocations:
    # Typed loosely so the raising variant below can reuse it.
    def __init__(self, clusters: Any) -> None:
        self._clusters = clusters

    def clusters(self) -> Any:
        return self._clusters


class _FakeContainerProjects:
    def __init__(self, locations: _FakeLocations) -> None:
        self._locations = locations

    def locations(self) -> _FakeLocations:
        return self._locations


class _FakeContainerService:
    def __init__(self, parents: list[str], pages: list[dict[str, Any]]) -> None:
        self._projects = _FakeContainerProjects(_FakeLocations(_FakeClusters(parents, pages)))

    def projects(self) -> _FakeContainerProjects:
        return self._projects


class _RaisingClusters:
    def __init__(self, parents: list[str], failing_project: str) -> None:
        self._parents = parents
        self._failing = failing_project
        self._parent = ""

    def list(self, parent: str) -> _RaisingClusters:
        self._parents.append(parent)
        self._parent = parent
        return self

    def execute(self) -> dict[str, Any]:
        if f"projects/{self._failing}/" in self._parent:
            raise RuntimeError("container.clusters.list denied")
        return {"clusters": [_cluster(name=f"c-{len(self._parents)}")]}


class _RaisingContainerService:
    def __init__(self, parents: list[str], failing_project: str) -> None:
        self._projects = _FakeContainerProjects(
            _FakeLocations(_RaisingClusters(parents, failing_project))
        )

    def projects(self) -> _FakeContainerProjects:
        return self._projects


def _install_container(
    monkeypatch: pytest.MonkeyPatch, service: Any, builds: list[Any] | None = None
) -> None:
    import integrations.gcp.tools.gcp_list_gke_clusters_tool as module

    def _build(config: Any, _api: tuple[str, str]) -> Any:
        if builds is not None:
            builds.append(config)
        return service

    monkeypatch.setattr(module, "build_service", _build)


def test_gke_tool_rejects_an_unknown_project() -> None:
    result = gcp_list_gke_clusters(
        project="nope", default_project="acme", available_projects=["acme"]
    )

    assert result["available"] is False
    assert "nope" in result["error"]


def test_gke_tool_lists_every_location_of_every_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parents: list[str] = []
    builds: list[Any] = []
    pages = [{"clusters": [_cluster()]}, {"clusters": [_cluster(name="staging")]}]
    _install_container(monkeypatch, _FakeContainerService(parents, pages), builds)

    result = gcp_list_gke_clusters(
        project="*",
        default_project="acme",
        available_projects=["acme", "acme-staging"],
        project_configs={"acme": _ONE_CREDENTIAL, "acme-staging": _ONE_CREDENTIAL},
    )

    assert parents == [
        "projects/acme/locations/-",
        "projects/acme-staging/locations/-",
    ]
    # One credential covers both projects, so only one client is built.
    assert len(builds) == 1
    assert result["cluster_count"] == 2


def test_gke_tool_builds_one_client_per_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    parents: list[str] = []
    builds: list[Any] = []
    pages: list[dict[str, Any]] = [{"clusters": []}, {"clusters": []}]
    _install_container(monkeypatch, _FakeContainerService(parents, pages), builds)

    gcp_list_gke_clusters(
        project="*",
        default_project="acme",
        available_projects=["acme", "research"],
        project_configs={"acme": _ONE_CREDENTIAL, "research": _OTHER_CREDENTIAL},
    )

    assert len(builds) == 2


def test_gke_tool_reports_which_clusters_kubectl_can_reach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [{"clusters": [_cluster(), _cluster(name="sandbox")]}]
    _install_container(monkeypatch, _FakeContainerService([], pages))

    result = gcp_list_gke_clusters(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
        registered_clusters=[{"name": "gke-prod", "context": "gke_acme_us-central1_prod"}],
    )

    by_name = {cluster["name"]: cluster for cluster in result["clusters"]}
    assert by_name["prod"]["registered_as"] == "gke-prod"
    assert result["unregistered_clusters"] == ["sandbox"]
    assert "gcp_logging_query" in result["note"]


def test_gke_tool_omits_the_note_when_everything_is_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_container(monkeypatch, _FakeContainerService([], [{"clusters": [_cluster()]}]))

    result = gcp_list_gke_clusters(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
        registered_clusters=[{"name": "prod", "context": ""}],
    )

    assert "unregistered_clusters" not in result
    assert "note" not in result


def test_gke_tool_surfaces_zones_it_could_not_reach(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [{"clusters": [_cluster()], "missingZones": ["us-central1-a"]}]
    _install_container(monkeypatch, _FakeContainerService([], pages))

    result = gcp_list_gke_clusters(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    # Otherwise "no clusters in us-central1-a" would read as fact.
    assert result["unreachable_locations"] == ["us-central1-a"]


def test_gke_tool_keeps_the_projects_that_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    parents: list[str] = []
    _install_container(monkeypatch, _RaisingContainerService(parents, "locked"))

    result = gcp_list_gke_clusters(
        project="*",
        default_project="acme",
        available_projects=["acme", "locked"],
        project_configs={"acme": _ONE_CREDENTIAL, "locked": _ONE_CREDENTIAL},
    )

    assert result["found"] is True
    assert result["cluster_count"] == 1
    assert result["partial_errors"] == ["locked: RuntimeError calling the Google API"]


def test_gke_tool_fails_when_no_project_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_container(monkeypatch, _RaisingContainerService([], "acme"))

    result = gcp_list_gke_clusters(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert result["found"] is False
    assert "acme" in result["error"]
    assert result["clusters"] == []


class _ApiOffResponse:
    status = 403


class _ApiOffError(Exception):
    """A 403 whose ``details[].reason`` says the API is off, not that access was denied."""

    resp = _ApiOffResponse()
    content = json.dumps(
        {
            "error": {
                "code": 403,
                "message": "Kubernetes Engine API has not been used in project x before",
                "status": "PERMISSION_DENIED",
                "details": [{"reason": "SERVICE_DISABLED", "domain": "googleapis.com"}],
            }
        }
    ).encode()


class _ApiOffClusters:
    def __init__(self, disabled_project: str) -> None:
        self._disabled = disabled_project
        self._parent = ""

    def list(self, parent: str) -> _ApiOffClusters:
        self._parent = parent
        return self

    def execute(self) -> dict[str, Any]:
        if f"projects/{self._disabled}/" in self._parent:
            raise _ApiOffError()
        return {"clusters": [_cluster()]}


class _ApiOffContainerService:
    def __init__(self, disabled_project: str) -> None:
        self._projects = _FakeContainerProjects(_FakeLocations(_ApiOffClusters(disabled_project)))

    def projects(self) -> _FakeContainerProjects:
        return self._projects


def test_gke_tool_says_nothing_about_a_project_with_the_api_switched_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A project with no GKE API holds no clusters, so there is no error to report.

    Left as a partial_error it spends the model's context on a non-problem, and
    on a wide project list it crowds out the projects that really did fail.
    """
    import integrations.gcp.tools.gcp_list_gke_clusters_tool as module

    reported: list[Exception] = []

    def _record(exc: Exception, **_kwargs: Any) -> None:
        reported.append(exc)

    monkeypatch.setattr(module, "report_run_error", _record)
    _install_container(monkeypatch, _ApiOffContainerService("no-gke"))

    result = gcp_list_gke_clusters(
        project="*",
        default_project="acme",
        available_projects=["acme", "no-gke"],
        project_configs={"acme": _ONE_CREDENTIAL, "no-gke": _ONE_CREDENTIAL},
    )

    assert result["cluster_count"] == 1
    assert "partial_errors" not in result
    # Nor is it worth an error-telemetry event on every sweep.
    assert reported == []


# --- audit filters -----------------------------------------------------------


def test_audit_filter_always_bounds_time_and_log_name() -> None:
    built = build_audit_filter(hours=2, now=_NOW)

    assert built == (
        'timestamp >= "2026-08-05T10:00:00Z" AND logName:"cloudaudit.googleapis.com%2Factivity"'
    )


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        ("data_access", "data_access"),
        ("DATA_ACCESS", "data_access"),
        ("", "activity"),
        ("nonsense", "activity"),
    ],
)
def test_normalize_log_type_widens_rather_than_failing(requested: str, expected: str) -> None:
    assert normalize_log_type(requested) == expected


def test_audit_filter_uses_the_bare_prefix_for_all_streams() -> None:
    built = build_audit_filter(log_type="all", now=_NOW)

    assert 'logName:"cloudaudit.googleapis.com"' in built


def test_audit_filter_adds_each_predicate() -> None:
    built = build_audit_filter(
        principal="deploy@acme.iam.gserviceaccount.com",
        method="compute.instances.delete",
        service="compute.googleapis.com",
        resource="instances/web-1",
        now=_NOW,
    )

    assert 'protoPayload.authenticationInfo.principalEmail:"deploy@acme' in built
    assert 'protoPayload.methodName:"compute.instances.delete"' in built
    assert 'protoPayload.serviceName:"compute.googleapis.com"' in built
    assert 'protoPayload.resourceName:"instances/web-1"' in built


def test_audit_filter_selects_failures_by_non_zero_code() -> None:
    built = build_audit_filter(failed_only=True, now=_NOW)

    # A successful entry omits status.code entirely, so `!= 0` is the test.
    assert "protoPayload.status.code != 0" in built


def test_quote_escapes_so_a_value_cannot_end_the_literal() -> None:
    assert quote('a"b\\c') == '"a\\"b\\\\c"'


def test_audit_filter_escapes_a_hostile_resource_name() -> None:
    built = build_audit_filter(resource='x" OR severity>=DEBUG OR resource="', now=_NOW)

    # The injected clause stays inside the string literal.
    assert "OR severity>=DEBUG" in built
    assert built.count("protoPayload.resourceName:") == 1
    assert '\\"' in built


# --- audit records -----------------------------------------------------------


def _audit_entry(**payload: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "authenticationInfo": {"principalEmail": "sre@acme.com"},
        "methodName": "v1.compute.instances.delete",
        "serviceName": "compute.googleapis.com",
        "resourceName": "projects/acme/zones/us-central1-a/instances/web-1",
        "requestMetadata": {"callerIp": "203.0.113.4", "callerSuppliedUserAgent": "gcloud/1.0"},
    }
    base.update(payload)
    return {
        "timestamp": "2026-08-05T11:59:00Z",
        "severity": "NOTICE",
        "protoPayload": base,
        "resource": {"type": "gce_instance", "labels": {"project_id": "acme"}},
    }


def test_normalize_record_answers_who_changed_what() -> None:
    record = normalize_record(_audit_entry())

    assert record["principal"] == "sre@acme.com"
    assert record["method"] == "v1.compute.instances.delete"
    assert record["resource"].endswith("instances/web-1")
    assert record["caller_ip"] == "203.0.113.4"
    assert record["project_id"] == "acme"
    assert record["succeeded"] is True
    assert record["status"] == "OK"


def test_normalize_record_renders_a_failure_status() -> None:
    record = normalize_record(_audit_entry(status={"code": 7, "message": "permission denied"}))

    assert record["succeeded"] is False
    assert record["status"] == "7: permission denied"


def test_normalize_record_names_the_missing_permission() -> None:
    record = normalize_record(
        _audit_entry(
            status={"code": 7},
            authorizationInfo=[
                {"permission": "compute.instances.delete", "granted": False},
                {"permission": "compute.instances.get", "granted": True},
            ],
        )
    )

    assert record["denied_permissions"] == ["compute.instances.delete"]


def test_normalize_record_flags_impersonation() -> None:
    record = normalize_record(
        _audit_entry(
            authenticationInfo={
                "principalEmail": "deploy@acme.iam.gserviceaccount.com",
                "serviceAccountDelegationInfo": [{"firstPartyPrincipal": {}}],
            }
        )
    )

    assert record["delegated"] is True


def test_normalize_record_keeps_the_operation_id() -> None:
    entry = _audit_entry()
    entry["operation"] = {"id": "operation-123", "last": True}

    record = normalize_record(entry)

    assert record["operation_id"] == "operation-123"
    assert record["operation_last"] is True


def test_normalize_record_tolerates_a_non_audit_entry() -> None:
    record = normalize_record({"timestamp": "2026-08-05T11:00:00Z", "textPayload": "hi"})

    assert record["principal"] == ""
    assert record["succeeded"] is True


# --- audit tool --------------------------------------------------------------


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


class _RaisingLoggingService:
    def entries(self) -> _RaisingLoggingService:
        return self

    def list(self, body: dict[str, Any]) -> _RaisingLoggingService:  # noqa: ARG002
        return self

    def execute(self) -> dict[str, Any]:
        raise RuntimeError("logging.entries.list denied")


def _install_logging(monkeypatch: pytest.MonkeyPatch, service: Any) -> None:
    import integrations.gcp.tools.gcp_audit_log_query_tool as module

    def _build(_config: Any, _api: tuple[str, str]) -> Any:
        return service

    monkeypatch.setattr(module, "build_service", _build)


def test_audit_tool_rejects_an_unknown_project() -> None:
    result = gcp_audit_log_query(
        project="nope", default_project="acme", available_projects=["acme"]
    )

    assert result["available"] is False
    assert result["records"] == []


def test_audit_tool_batches_one_credential_into_one_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    _install_logging(monkeypatch, _FakeLoggingService(calls, [{"entries": [_audit_entry()]}]))

    result = gcp_audit_log_query(
        project="*",
        default_project="acme",
        available_projects=["acme", "acme-staging"],
        project_configs={"acme": _ONE_CREDENTIAL, "acme-staging": _ONE_CREDENTIAL},
    )

    assert len(calls) == 1
    assert calls[0]["resourceNames"] == ["projects/acme", "projects/acme-staging"]
    assert 'logName:"cloudaudit.googleapis.com%2Factivity"' in calls[0]["filter"]
    assert result["record_count"] == 1


def test_audit_tool_merges_credentials_newest_first(monkeypatch: pytest.MonkeyPatch) -> None:
    older = _audit_entry()
    older["timestamp"] = "2026-08-05T10:00:00Z"
    newer = _audit_entry()
    newer["timestamp"] = "2026-08-05T11:00:00Z"
    _install_logging(
        monkeypatch, _FakeLoggingService([], [{"entries": [older]}, {"entries": [newer]}])
    )

    result = gcp_audit_log_query(
        project="*",
        default_project="acme",
        available_projects=["acme", "research"],
        project_configs={"acme": _ONE_CREDENTIAL, "research": _OTHER_CREDENTIAL},
    )

    assert [record["timestamp"] for record in result["records"]] == [
        "2026-08-05T11:00:00Z",
        "2026-08-05T10:00:00Z",
    ]


def test_audit_tool_explains_an_empty_data_access_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_logging(monkeypatch, _FakeLoggingService([], [{"entries": []}]))

    result = gcp_audit_log_query(
        log_type="data_access",
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert result["found"] is False
    assert "disabled by default" in result["note"]


def test_audit_tool_does_not_explain_an_empty_activity_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_logging(monkeypatch, _FakeLoggingService([], [{"entries": []}]))

    result = gcp_audit_log_query(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert "note" not in result


def test_audit_tool_reports_an_api_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_logging(monkeypatch, _RaisingLoggingService())

    result = gcp_audit_log_query(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert result["found"] is False
    assert result["error"] == "RuntimeError calling the Google API"
    assert result["records"] == []


# --- compute normalization ---------------------------------------------------


def _instance(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "web-1",
        "zone": "https://www.googleapis.com/compute/v1/projects/acme/zones/us-central1-a",
        "machineType": (
            "https://www.googleapis.com/compute/v1/projects/acme/zones/"
            "us-central1-a/machineTypes/e2-standard-4"
        ),
        "status": "RUNNING",
        "creationTimestamp": "2026-01-02T03:04:05.000-08:00",
        "networkInterfaces": [
            {"networkIP": "10.0.0.4", "accessConfigs": [{"natIP": "203.0.113.9"}]}
        ],
    }
    base.update(overrides)
    return base


def test_normalize_instance_shortens_the_self_links() -> None:
    normalized = normalize_instance(_instance(), "acme")

    assert normalized["zone"] == "us-central1-a"
    assert normalized["machine_type"] == "e2-standard-4"
    assert normalized["internal_ip"] == "10.0.0.4"
    assert normalized["external_ip"] == "203.0.113.9"


def test_normalize_instance_omits_a_missing_external_ip() -> None:
    normalized = normalize_instance(
        _instance(networkInterfaces=[{"networkIP": "10.0.0.5"}]), "acme"
    )

    assert "external_ip" not in normalized


def test_normalize_instance_names_the_gke_cluster_a_node_belongs_to() -> None:
    normalized = normalize_instance(
        _instance(
            labels={"goog-k8s-cluster-name": "prod", "goog-k8s-node-pool-name": "default-pool"}
        ),
        "acme",
    )

    assert normalized["gke_cluster"] == "prod"
    assert normalized["gke_node_pool"] == "default-pool"


@pytest.mark.parametrize("scheduling", [{"preemptible": True}, {"provisioningModel": "SPOT"}])
def test_normalize_instance_flags_a_vm_that_can_vanish(scheduling: dict[str, Any]) -> None:
    normalized = normalize_instance(_instance(scheduling=scheduling), "acme")

    assert normalized["preemptible"] is True


def test_normalize_instance_keeps_network_tags() -> None:
    normalized = normalize_instance(_instance(tags={"items": ["allow-https"]}), "acme")

    assert normalized["network_tags"] == ["allow-https"]


def test_flatten_aggregated_skips_scopes_that_only_carry_a_warning() -> None:
    items = {
        "zones/us-central1-a": {"instances": [_instance()]},
        "zones/europe-west1-b": {"warning": {"code": "NO_RESULTS_ON_PAGE"}},
    }

    assert [item["name"] for item in flatten_aggregated(items, "acme")] == ["web-1"]


def test_flatten_aggregated_tolerates_a_missing_items_map() -> None:
    assert flatten_aggregated(None, "acme") == []


# --- compute tool ------------------------------------------------------------


class _FakeInstances:
    def __init__(self, calls: list[dict[str, Any]], pages: list[dict[str, Any]]) -> None:
        self._calls = calls
        self._pages = pages

    def aggregatedList(  # noqa: N802 — matches the Compute API method name
        self,
        project: str,
        maxResults: int,
        filter: str | None,  # noqa: N803, A002
    ) -> _FakeInstances:
        self._calls.append({"project": project, "maxResults": maxResults, "filter": filter})
        return self

    def execute(self) -> dict[str, Any]:
        return self._pages.pop(0)


class _FakeComputeService:
    def __init__(self, calls: list[dict[str, Any]], pages: list[dict[str, Any]]) -> None:
        self._instances = _FakeInstances(calls, pages)

    def instances(self) -> _FakeInstances:
        return self._instances


class _RaisingInstances:
    def __init__(self, failing_project: str) -> None:
        self._failing = failing_project
        self._project = ""

    def aggregatedList(  # noqa: N802 — matches the Compute API method name
        self,
        project: str,
        maxResults: int,  # noqa: N803, ARG002
        filter: str | None,  # noqa: A002, ARG002
    ) -> _RaisingInstances:
        self._project = project
        return self

    def execute(self) -> dict[str, Any]:
        if self._project == self._failing:
            raise RuntimeError("compute.instances.list denied")
        return {"items": {"zones/us-central1-a": {"instances": [_instance()]}}}


class _RaisingComputeService:
    def __init__(self, failing_project: str) -> None:
        self._instances = _RaisingInstances(failing_project)

    def instances(self) -> _RaisingInstances:
        return self._instances


def _install_compute(monkeypatch: pytest.MonkeyPatch, service: Any) -> None:
    import integrations.gcp.tools.gcp_list_compute_instances_tool as module

    def _build(_config: Any, _api: tuple[str, str]) -> Any:
        return service

    monkeypatch.setattr(module, "build_service", _build)


def test_compute_tool_rejects_an_unknown_status() -> None:
    result = gcp_list_compute_instances(
        status="ON_FIRE", default_project="acme", available_projects=["acme"]
    )

    assert result["available"] is False
    assert "ON_FIRE" in result["error"]


def test_compute_tool_pushes_the_status_filter_to_the_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    _install_compute(monkeypatch, _FakeComputeService(calls, [{"items": {}}]))

    gcp_list_compute_instances(
        status="running",
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert calls[0]["filter"] == 'status = "RUNNING"'


def test_compute_tool_sends_no_filter_when_unfiltered(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    _install_compute(monkeypatch, _FakeComputeService(calls, [{"items": {}}]))

    gcp_list_compute_instances(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert calls[0]["filter"] is None


def test_compute_tool_matches_a_name_substring(monkeypatch: pytest.MonkeyPatch) -> None:
    page = {
        "items": {
            "zones/us-central1-a": {
                "instances": [_instance(), _instance(name="db-1")],
            }
        }
    }
    _install_compute(monkeypatch, _FakeComputeService([], [page]))

    result = gcp_list_compute_instances(
        name_contains="DB",
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert [item["name"] for item in result["instances"]] == ["db-1"]


def test_compute_tool_surfaces_scopes_it_could_not_reach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = {"items": {}, "unreachables": ["zones/us-central1-a"]}
    _install_compute(monkeypatch, _FakeComputeService([], [page]))

    result = gcp_list_compute_instances(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert result["unreachable_scopes"] == ["zones/us-central1-a"]


def test_compute_tool_keeps_the_projects_that_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_compute(monkeypatch, _RaisingComputeService("locked"))

    result = gcp_list_compute_instances(
        project="*",
        default_project="acme",
        available_projects=["acme", "locked"],
        project_configs={"acme": _ONE_CREDENTIAL, "locked": _ONE_CREDENTIAL},
    )

    assert result["found"] is True
    assert result["instance_count"] == 1
    assert result["partial_errors"] == ["locked: RuntimeError calling the Google API"]


def test_compute_tool_fails_when_no_project_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_compute(monkeypatch, _RaisingComputeService("acme"))

    result = gcp_list_compute_instances(
        default_project="acme",
        available_projects=["acme"],
        project_configs={"acme": _ONE_CREDENTIAL},
    )

    assert result["found"] is False
    assert result["instances"] == []
