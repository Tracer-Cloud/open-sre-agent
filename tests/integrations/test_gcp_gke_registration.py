"""GKE auto-registration: kubeconfig synthesis, discovery, and the register flow."""

from __future__ import annotations

import json
from typing import Any

import pytest
import yaml

from integrations.gcp.gke import discovery as discovery_module
from integrations.gcp.gke import registration as registration_module
from integrations.gcp.gke.discovery import DiscoveredCluster, Discovery, discover_clusters
from integrations.gcp.gke.kubeconfig import (
    AUTH_PLUGIN,
    build_kubeconfig,
    credentials_path_for,
    plugin_installed,
)
from integrations.gcp.gke.registration import Outcome, register_gke_clusters
from integrations.gcp.gke.scope import parse_scopes
from integrations.kubernetes.clusters import ClusterResult, ClusterSummary

_CA = "LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0t"
_ONE_CREDENTIAL = {"project_id": "acme"}
_OTHER_CREDENTIAL = {"project_id": "research", "impersonate_service_account": "ro@x"}


def _resolved(*, key: str = "", impersonate: str = "") -> dict[str, Any]:
    """A classified-integrations dict with one GCP credential over two projects."""
    config: dict[str, Any] = {
        "project_id": "acme",
        "additional_projects": ["acme-staging"],
    }
    if key:
        config["service_account_key"] = key
    if impersonate:
        config["impersonate_service_account"] = impersonate
    return {"gcp": config}


def _raw_cluster(
    *,
    name: str = "prod",
    project_location: str = "us-central1",
    endpoint: str = "10.0.0.1",
    ca: str = _CA,
    private: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "location": project_location,
        "endpoint": endpoint,
        "status": "RUNNING",
        "masterAuth": {"clusterCaCertificate": ca},
        "privateClusterConfig": {"enablePrivateEndpoint": private},
    }


def _cluster(
    *,
    name: str = "prod",
    project: str = "acme",
    location: str = "us-central1",
    endpoint: str = "10.0.0.1",
    ca: str = _CA,
    private: bool = False,
) -> DiscoveredCluster:
    return DiscoveredCluster(
        project=project,
        name=name,
        location=location,
        endpoint=endpoint,
        ca_certificate=ca,
        status="RUNNING",
        private_endpoint_only=private,
    )


# --- kubeconfig synthesis ----------------------------------------------------


def test_kubeconfig_names_cluster_context_and_user_alike() -> None:
    document = yaml.safe_load(
        build_kubeconfig(
            context="gke_acme_us-central1_prod", endpoint="10.0.0.1", ca_certificate=_CA
        )
    )

    context = "gke_acme_us-central1_prod"
    assert document["current-context"] == context
    assert document["clusters"][0]["name"] == context
    assert document["contexts"][0]["context"] == {"cluster": context, "user": context}
    assert document["users"][0]["name"] == context


def test_kubeconfig_carries_the_endpoint_over_https_and_the_ca_verbatim() -> None:
    document = yaml.safe_load(
        build_kubeconfig(context="c", endpoint="34.10.0.5", ca_certificate=_CA)
    )

    cluster = document["clusters"][0]["cluster"]
    assert cluster["server"] == "https://34.10.0.5"
    # container/v1 already base64-encodes it, which is what the field expects.
    assert cluster["certificate-authority-data"] == _CA


def test_kubeconfig_delegates_auth_to_the_plugin_and_stores_no_secret() -> None:
    rendered = build_kubeconfig(context="c", endpoint="10.0.0.1", ca_certificate=_CA)
    exec_block = yaml.safe_load(rendered)["users"][0]["user"]["exec"]

    assert exec_block["command"] == AUTH_PLUGIN
    assert exec_block["provideClusterInfo"] is True
    assert exec_block["apiVersion"] == "client.authentication.k8s.io/v1beta1"
    # No token, no client key, no password anywhere in the document.
    for secret_field in ("token", "client-key-data", "password", "auth-provider"):
        assert secret_field not in rendered


def test_kubeconfig_pins_the_plugin_to_a_service_account_key_file() -> None:
    document = yaml.safe_load(
        build_kubeconfig(
            context="c",
            endpoint="10.0.0.1",
            ca_certificate=_CA,
            credentials_path="/keys/sa.json",
        )
    )

    assert document["users"][0]["user"]["exec"]["env"] == [
        {"name": "GOOGLE_APPLICATION_CREDENTIALS", "value": "/keys/sa.json"}
    ]


def test_kubeconfig_omits_the_env_block_when_there_is_no_key_file() -> None:
    document = yaml.safe_load(
        build_kubeconfig(context="c", endpoint="10.0.0.1", ca_certificate=_CA)
    )

    assert "env" not in document["users"][0]["user"]["exec"]


def test_the_plugin_is_told_to_resolve_adc_itself() -> None:
    """Without the flag the plugin shells out to ``gcloud``.

    In the container image gcloud is not installed, so the plugin exits
    non-zero, the Kubernetes client sends no bearer token, and the API server
    answers ``pods is forbidden: User "system:anonymous"`` — a 403 that reads
    like a missing RBAC binding rather than a missing credential.
    """
    document = yaml.safe_load(
        build_kubeconfig(context="c", endpoint="10.0.0.1", ca_certificate=_CA)
    )

    assert document["users"][0]["user"]["exec"]["args"] == ["--use_application_default_credentials"]


def test_impersonation_is_forwarded_to_the_plugin() -> None:
    """The plugin must present the account the GCP API clients present.

    ``resolve_credentials`` layers impersonation over the ambient credential, so
    a kubeconfig built without it authenticates as the *node's* identity while
    discovery ran as the impersonated one.
    """
    document = yaml.safe_load(
        build_kubeconfig(
            context="c",
            endpoint="10.0.0.1",
            ca_certificate=_CA,
            impersonate_service_account="sre@acme.iam.gserviceaccount.com",
        )
    )

    assert document["users"][0]["user"]["exec"]["args"] == [
        "--use_application_default_credentials",
        "--impersonate_service_account=sre@acme.iam.gserviceaccount.com",
    ]


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("/keys/sa.json", "/keys/sa.json"),
        ("  /keys/sa.json  ", "/keys/sa.json"),
        ('{"type": "service_account"}', ""),
        ("", ""),
    ],
)
def test_only_a_key_path_can_be_pinned_not_inline_json(key: str, expected: str) -> None:
    assert credentials_path_for(key) == expected


def test_plugin_installed_follows_path_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    def _which_found(_name: str) -> str:
        return "/usr/bin/gke-gcloud-auth-plugin"

    def _which_missing(_name: str) -> None:
        return None

    monkeypatch.setattr("integrations.gcp.gke.kubeconfig.shutil.which", _which_found)
    assert plugin_installed() is True

    monkeypatch.setattr("integrations.gcp.gke.kubeconfig.shutil.which", _which_missing)
    assert plugin_installed() is False


# --- discovery ---------------------------------------------------------------


class _FakeClusters:
    def __init__(self, parents: list[str], pages: list[dict[str, Any]]) -> None:
        self._parents = parents
        self._pages = pages

    def list(self, parent: str) -> _FakeClusters:
        self._parents.append(parent)
        return self

    def execute(self) -> dict[str, Any]:
        return self._pages.pop(0) if self._pages else {}


class _FakeLocations:
    def __init__(self, clusters: Any) -> None:
        self._clusters = clusters

    def clusters(self) -> Any:
        return self._clusters


class _FakeProjects:
    def __init__(self, locations: _FakeLocations) -> None:
        self._locations = locations

    def locations(self) -> _FakeLocations:
        return self._locations


class _FakeContainerService:
    def __init__(self, parents: list[str], pages: list[dict[str, Any]]) -> None:
        self._projects = _FakeProjects(_FakeLocations(_FakeClusters(parents, pages)))

    def projects(self) -> _FakeProjects:
        return self._projects


class _RaisingClusters:
    def __init__(self, failing_project: str) -> None:
        self._failing = failing_project
        self._parent = ""

    def list(self, parent: str) -> _RaisingClusters:
        self._parent = parent
        return self

    def execute(self) -> dict[str, Any]:
        if f"projects/{self._failing}/" in self._parent:
            raise RuntimeError("container.clusters.list denied")
        return {"clusters": [_raw_cluster()]}


class _RaisingContainerService:
    def __init__(self, failing_project: str) -> None:
        self._projects = _FakeProjects(_FakeLocations(_RaisingClusters(failing_project)))

    def projects(self) -> _FakeProjects:
        return self._projects


def _install_discovery(
    monkeypatch: pytest.MonkeyPatch, service: Any, builds: list[Any] | None = None
) -> None:
    def _build(config: Any, _api: tuple[str, str]) -> Any:
        if builds is not None:
            builds.append(config)
        return service

    monkeypatch.setattr(discovery_module, "build_service", _build)


def test_discovery_asks_every_location_of_every_project(monkeypatch: pytest.MonkeyPatch) -> None:
    parents: list[str] = []
    builds: list[Any] = []
    pages = [{"clusters": [_raw_cluster()]}, {"clusters": [_raw_cluster(name="staging")]}]
    _install_discovery(monkeypatch, _FakeContainerService(parents, pages), builds)

    discovered = discover_clusters(
        ["acme", "acme-staging"],
        {"acme": _ONE_CREDENTIAL, "acme-staging": _ONE_CREDENTIAL},
    )

    assert parents == ["projects/acme/locations/-", "projects/acme-staging/locations/-"]
    # One credential reaches both projects, so authentication happens once.
    assert len(builds) == 1
    assert [cluster.name for cluster in discovered.clusters] == ["prod", "staging"]
    assert discovered.errors == []
    assert discovered.no_gke == []


def test_discovery_builds_one_client_per_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    builds: list[Any] = []
    pages: list[dict[str, Any]] = [{"clusters": []}, {"clusters": []}]
    _install_discovery(monkeypatch, _FakeContainerService([], pages), builds)

    discover_clusters(
        ["acme", "research"], {"acme": _ONE_CREDENTIAL, "research": _OTHER_CREDENTIAL}
    )

    assert len(builds) == 2


def test_discovery_captures_the_endpoint_and_ca_the_tool_drops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [{"clusters": [_raw_cluster(endpoint="34.1.2.3", private=True)]}]
    _install_discovery(monkeypatch, _FakeContainerService([], pages))

    clusters = discover_clusters(["acme"], {"acme": _ONE_CREDENTIAL}).clusters

    assert clusters[0].endpoint == "34.1.2.3"
    assert clusters[0].ca_certificate == _CA
    assert clusters[0].private_endpoint_only is True
    assert clusters[0].context == "gke_acme_us-central1_prod"
    assert clusters[0].running is True


def test_discovery_falls_back_to_the_deprecated_zone_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_cluster()
    del raw["location"]
    raw["zone"] = "us-east1-b"
    _install_discovery(monkeypatch, _FakeContainerService([], [{"clusters": [raw]}]))

    clusters = discover_clusters(["acme"], {"acme": _ONE_CREDENTIAL}).clusters

    assert clusters[0].location == "us-east1-b"
    assert clusters[0].context == "gke_acme_us-east1-b_prod"


def test_discovery_tolerates_a_cluster_with_no_master_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_cluster()
    del raw["masterAuth"]
    _install_discovery(monkeypatch, _FakeContainerService([], [{"clusters": [raw]}]))

    clusters = discover_clusters(["acme"], {"acme": _ONE_CREDENTIAL}).clusters

    assert clusters[0].ca_certificate == ""


def test_one_denied_project_does_not_discard_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_discovery(monkeypatch, _RaisingContainerService("acme-staging"))

    discovered = discover_clusters(
        ["acme", "acme-staging"],
        {"acme": _ONE_CREDENTIAL, "acme-staging": _ONE_CREDENTIAL},
    )

    assert len(discovered.clusters) == 1
    assert len(discovered.errors) == 1
    assert "acme-staging" in discovered.errors[0]
    assert discovered.no_gke == []


class _ApiOffResponse:
    status = 403


class _ApiOffError(Exception):
    """What ``container/v1`` raises when the project never enabled the API.

    Same 403 and same ``PERMISSION_DENIED`` status as a real denial — only
    ``details[].reason`` tells them apart.
    """

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
        return {"clusters": [_raw_cluster()]}


class _ApiOffContainerService:
    def __init__(self, disabled_project: str) -> None:
        self._projects = _FakeProjects(_FakeLocations(_ApiOffClusters(disabled_project)))

    def projects(self) -> _FakeProjects:
        return self._projects


def test_a_project_without_the_api_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A project that never enabled GKE cannot hold a cluster, so it has nothing to report.

    Counting it as a failure is what buries the projects that genuinely could
    not be read, and on a discovered project list most of the estate is this.
    """
    _install_discovery(monkeypatch, _ApiOffContainerService("acme-staging"))

    discovered = discover_clusters(
        ["acme", "acme-staging"],
        {"acme": _ONE_CREDENTIAL, "acme-staging": _ONE_CREDENTIAL},
    )

    assert [cluster.project for cluster in discovered.clusters] == ["acme"]
    assert discovered.errors == []
    assert discovered.no_gke == ["acme-staging"]


def test_a_credential_that_cannot_be_built_reports_its_whole_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _build(_config: Any, _api: tuple[str, str]) -> Any:
        raise discovery_module.GCPClientError("no Google credentials available")

    monkeypatch.setattr(discovery_module, "build_service", _build)

    discovered = discover_clusters(
        ["acme", "acme-staging"],
        {"acme": _ONE_CREDENTIAL, "acme-staging": _ONE_CREDENTIAL},
    )

    assert discovered.clusters == []
    assert discovered.errors == ["acme, acme-staging: no Google credentials available"]


# --- registration ------------------------------------------------------------


class _RecordingStore:
    """Stands in for the Kubernetes cluster store: records every add_cluster call."""

    def __init__(self, existing: list[ClusterSummary] | None = None, ok: bool = True) -> None:
        self.existing = existing or []
        self.ok = ok
        self.calls: list[dict[str, Any]] = []

    def list_clusters(self) -> list[ClusterSummary]:
        return self.existing

    def add_cluster(self, **kwargs: Any) -> ClusterResult:
        self.calls.append(kwargs)
        name = kwargs["name"]
        return ClusterResult(
            ok=self.ok,
            detail=f"Cluster '{name}' registered." if self.ok else "connection refused",
        )


def _install_registration(
    monkeypatch: pytest.MonkeyPatch,
    store: _RecordingStore,
    clusters: list[DiscoveredCluster],
    errors: list[str] | None = None,
    no_gke: list[str] | None = None,
) -> None:
    def _discover(_projects: list[str], _configs: dict[str, Any] | None) -> Discovery:
        return Discovery(clusters=clusters, errors=list(errors or []), no_gke=list(no_gke or []))

    monkeypatch.setattr(registration_module, "discover_clusters", _discover)
    monkeypatch.setattr(registration_module, "list_clusters", store.list_clusters)
    monkeypatch.setattr(registration_module, "add_cluster", store.add_cluster)


def test_a_discovered_cluster_becomes_a_named_kubernetes_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _RecordingStore()
    _install_registration(monkeypatch, store, [_cluster()])

    report = register_gke_clusters(resolved=_resolved())

    assert report.count(Outcome.REGISTERED) == 1
    call = store.calls[0]
    assert call["name"] == "prod"
    assert call["context"] == "gke_acme_us-central1_prod"
    assert call["tags"] == {"source": "gke", "project": "acme", "location": "us-central1"}
    assert yaml.safe_load(call["kubeconfig"])["current-context"] == "gke_acme_us-central1_prod"


def test_the_stored_kubeconfig_pins_the_key_file_opensre_discovered_with(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _RecordingStore()
    _install_registration(monkeypatch, store, [_cluster()])

    register_gke_clusters(resolved=_resolved(key="/keys/sa.json"))

    exec_block = yaml.safe_load(store.calls[0]["kubeconfig"])["users"][0]["user"]["exec"]
    assert exec_block["env"] == [
        {"name": "GOOGLE_APPLICATION_CREDENTIALS", "value": "/keys/sa.json"}
    ]


def test_the_stored_kubeconfig_carries_the_impersonation_discovery_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovery and connection must present one identity.

    ``resolve_credentials`` impersonates for the API clients, so a kubeconfig
    that omits the flag authenticates as the ambient credential — which may be
    able to *list* the cluster while having no RBAC inside it.
    """
    store = _RecordingStore()
    _install_registration(monkeypatch, store, [_cluster()])

    register_gke_clusters(resolved=_resolved(impersonate="sre@acme.iam.gserviceaccount.com"))

    exec_block = yaml.safe_load(store.calls[0]["kubeconfig"])["users"][0]["user"]["exec"]
    assert exec_block["args"] == [
        "--use_application_default_credentials",
        "--impersonate_service_account=sre@acme.iam.gserviceaccount.com",
    ]


def test_an_inline_service_account_key_never_reaches_the_kubeconfig(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _RecordingStore()
    _install_registration(monkeypatch, store, [_cluster()])
    secret = '{"type": "service_account", "private_key": "SUPER-SECRET"}'

    register_gke_clusters(resolved=_resolved(key=secret))

    assert "SUPER-SECRET" not in store.calls[0]["kubeconfig"]
    assert "env" not in yaml.safe_load(store.calls[0]["kubeconfig"])["users"][0]["user"]["exec"]


def test_user_tags_win_over_the_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _RecordingStore()
    _install_registration(monkeypatch, store, [_cluster()])

    register_gke_clusters(resolved=_resolved(), tags={"env": "prod", "source": "manual"})

    assert store.calls[0]["tags"]["env"] == "prod"
    assert store.calls[0]["tags"]["source"] == "manual"


def test_rerunning_skips_a_cluster_that_is_already_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = [
        ClusterSummary(
            name="gke-prod", tags={}, context="gke_acme_us-central1_prod", namespace="default"
        )
    ]
    store = _RecordingStore(existing=existing)
    _install_registration(monkeypatch, store, [_cluster()])

    report = register_gke_clusters(resolved=_resolved())

    assert store.calls == []
    assert report.count(Outcome.SKIPPED) == 1
    # Reported under the name it is actually registered as, not the name we
    # would have chosen.
    assert report.results[0].instance == "gke-prod"
    assert report.results[0].detail == "already registered"


def test_a_name_already_pointing_elsewhere_is_not_silently_repointed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = [
        ClusterSummary(name="prod", tags={}, context="eks_prod_cluster", namespace="default")
    ]
    store = _RecordingStore(existing=existing)
    _install_registration(monkeypatch, store, [_cluster()])

    report = register_gke_clusters(resolved=_resolved())

    assert store.calls == []
    assert report.count(Outcome.SKIPPED) == 1
    assert "--overwrite" in report.results[0].detail


def test_overwrite_replaces_a_conflicting_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = [
        ClusterSummary(name="prod", tags={}, context="eks_prod_cluster", namespace="default")
    ]
    store = _RecordingStore(existing=existing)
    _install_registration(monkeypatch, store, [_cluster()])

    report = register_gke_clusters(resolved=_resolved(), overwrite=True)

    assert report.count(Outcome.REGISTERED) == 1
    assert store.calls[0]["name"] == "prod"


def test_a_name_claimed_by_two_projects_is_qualified_with_the_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _RecordingStore()
    _install_registration(
        monkeypatch,
        store,
        [_cluster(project="acme"), _cluster(project="acme-staging")],
    )

    report = register_gke_clusters(resolved=_resolved())

    assert [call["name"] for call in store.calls] == ["prod-acme", "prod-acme-staging"]
    assert report.count(Outcome.REGISTERED) == 2


def test_an_unambiguous_name_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _RecordingStore()
    _install_registration(
        monkeypatch,
        store,
        [_cluster(name="prod"), _cluster(name="staging", project="acme-staging")],
    )

    register_gke_clusters(resolved=_resolved())

    assert [call["name"] for call in store.calls] == ["prod", "staging"]


def test_a_cluster_registered_this_run_blocks_a_later_name_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _RecordingStore()
    # Same name, same project, different location: the collision only exists
    # once the first one has been written.
    _install_registration(
        monkeypatch,
        store,
        [_cluster(location="us-central1"), _cluster(location="europe-west1")],
    )

    report = register_gke_clusters(resolved=_resolved())

    assert len(store.calls) == 1
    assert report.count(Outcome.REGISTERED) == 1
    assert report.count(Outcome.SKIPPED) == 1


def test_a_cluster_with_no_endpoint_or_ca_fails_rather_than_storing_a_dead_kubeconfig(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _RecordingStore()
    _install_registration(monkeypatch, store, [_cluster(ca="")])

    report = register_gke_clusters(resolved=_resolved())

    assert store.calls == []
    assert report.count(Outcome.FAILED) == 1
    assert "CA certificate" in report.results[0].detail


def test_a_failed_probe_on_a_private_cluster_explains_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _RecordingStore(ok=False)
    _install_registration(monkeypatch, store, [_cluster(private=True)])

    report = register_gke_clusters(resolved=_resolved())

    assert report.count(Outcome.FAILED) == 1
    assert "private endpoint" in report.results[0].detail


def test_dry_run_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _RecordingStore()
    _install_registration(monkeypatch, store, [_cluster()])

    report = register_gke_clusters(resolved=_resolved(), dry_run=True)

    assert store.calls == []
    assert report.count(Outcome.REGISTERED) == 1
    assert report.results[0].detail == "would register"


def test_verify_flag_is_passed_through_to_the_store(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _RecordingStore()
    _install_registration(monkeypatch, store, [_cluster()])

    register_gke_clusters(resolved=_resolved(), verify=False)

    assert store.calls[0]["verify"] is False


def test_an_unknown_project_is_rejected_before_any_api_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _RecordingStore()
    _install_registration(monkeypatch, store, [_cluster()])

    report = register_gke_clusters(resolved=_resolved(), project="nope")

    assert store.calls == []
    assert report.results == []
    assert "nope" in report.errors[0]


def test_discovery_errors_surface_on_the_report(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _RecordingStore()
    _install_registration(monkeypatch, store, [], errors=["acme: HTTP 403: denied"])

    report = register_gke_clusters(resolved=_resolved())

    assert report.errors == ["acme: HTTP 403: denied"]
    assert report.results == []


def test_projects_without_the_api_are_named_but_kept_out_of_the_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Still named: the way a project selector goes wrong is a project that turns out empty."""
    store = _RecordingStore()
    _install_registration(monkeypatch, store, [_cluster()], no_gke=["acme-staging"])

    report = register_gke_clusters(resolved=_resolved())

    assert report.errors == []
    assert report.no_gke == ["acme-staging"]
    assert report.count(Outcome.REGISTERED) == 1


def test_no_gcp_configured_reports_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _RecordingStore()
    _install_registration(monkeypatch, store, [_cluster()])

    report = register_gke_clusters(resolved={})

    assert store.calls == []
    assert "GCP_PROJECT_ID" in report.errors[0]


# --- cluster scope -----------------------------------------------------------


def test_a_scope_registers_only_the_cluster_it_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """Project granularity is all-or-nothing; this is the reason the filter exists.

    A project holding one cluster worth investigating and several that are not
    the agent's business used to force the operator to choose between handing it
    everything and opting out entirely.
    """
    store = _RecordingStore()
    _install_registration(monkeypatch, store, [_cluster(name="checkout"), _cluster(name="billing")])

    report = register_gke_clusters(
        resolved=_resolved(), project="acme", cluster_scope=parse_scopes("acme/checkout")
    )

    assert [call["name"] for call in store.calls] == ["checkout"]
    assert report.count(Outcome.REGISTERED) == 1
    assert report.excluded == ["billing (acme)"]


def test_no_scope_still_registers_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _RecordingStore()
    _install_registration(monkeypatch, store, [_cluster(name="checkout"), _cluster(name="billing")])

    report = register_gke_clusters(resolved=_resolved())

    assert sorted(call["name"] for call in store.calls) == ["billing", "checkout"]
    assert report.excluded == []


def test_a_scope_that_matches_nothing_registers_nothing_and_says_what_it_saw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The realistic failure is a typo, and "registered 0" alone does not reveal one."""
    store = _RecordingStore()
    _install_registration(monkeypatch, store, [_cluster(name="checkout")])

    report = register_gke_clusters(
        resolved=_resolved(), project="acme", cluster_scope=parse_scopes("acme/chekout")
    )

    assert store.calls == []
    assert report.excluded == ["checkout (acme)"]


def test_a_scope_qualifies_by_project_so_a_shared_name_is_not_swept_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two clusters called `prod` in different projects is ordinary, not a request for both."""
    store = _RecordingStore()
    _install_registration(
        monkeypatch,
        store,
        [_cluster(name="prod"), _cluster(name="prod", project="acme-staging")],
    )

    register_gke_clusters(
        resolved=_resolved(), project="acme,acme-staging", cluster_scope=parse_scopes("acme/prod")
    )

    assert [(call["name"], call["tags"]["project"]) for call in store.calls] == [("prod", "acme")]


def test_a_wildcard_project_scope_takes_the_named_cluster_from_every_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _RecordingStore()
    _install_registration(
        monkeypatch,
        store,
        [
            _cluster(name="prod"),
            _cluster(name="prod", project="acme-staging"),
            _cluster(name="dev"),
        ],
    )

    register_gke_clusters(resolved=_resolved(), project="*", cluster_scope=parse_scopes("*/prod"))

    # Both survive the filter, so the shared name is still ambiguous and both get
    # qualified — the pre-existing rule, unchanged by scoping.
    assert sorted(call["name"] for call in store.calls) == ["prod-acme", "prod-acme-staging"]


def test_an_excluded_cluster_does_not_force_a_project_suffix_on_the_one_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Names are assigned after filtering, so the scope does not change what you type.

    Qualifying the kept cluster because of one the operator explicitly excluded
    would mean narrowing the scope silently renames the instance the agent must
    address.
    """
    store = _RecordingStore()
    _install_registration(
        monkeypatch,
        store,
        [_cluster(name="prod"), _cluster(name="prod", project="acme-staging")],
    )

    register_gke_clusters(
        resolved=_resolved(), project="acme,acme-staging", cluster_scope=parse_scopes("acme/prod")
    )

    assert store.calls[0]["name"] == "prod"
