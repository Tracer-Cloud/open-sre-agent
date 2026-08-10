"""Tests for Kubernetes investigation tools."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from kubernetes.client.exceptions import ApiException

from core.execution import execute_tool_calls
from core.llm.types import ToolCall
from core.tool_framework.registered_tool import RegisteredTool
from integrations.kubernetes.client import _RESOURCE_DISPATCH
from integrations.kubernetes.tools import (
    KubernetesDescribePodTool,
    KubernetesGetEventsTool,
    KubernetesGetPodLogsTool,
    KubernetesGetResourceTool,
    KubernetesListClustersTool,
    KubernetesListConfigMapsTool,
    KubernetesListDaemonSetsTool,
    KubernetesListDeploymentsTool,
    KubernetesListIngressesTool,
    KubernetesListNamespacesTool,
    KubernetesListNodesTool,
    KubernetesListPodsTool,
    KubernetesListRolloutsTool,
    KubernetesListServicesTool,
    KubernetesListStatefulSetsTool,
    KubernetesListWorkloadsTool,
)
from tests.tools.conftest import BaseToolContract, mock_agent_state

_MINIMAL_KUBECONFIG = (
    "apiVersion: v1\n"
    "clusters: []\n"
    "contexts: []\n"
    "current-context: ''\n"
    "kind: Config\n"
    "preferences: {}\n"
    "users: []\n"
)

_K8S_SOURCE = {
    "connection_verified": True,
    "kubeconfig": _MINIMAL_KUBECONFIG,
    "context": "",
    "namespace": "default",
}


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------


class TestKubernetesListPodsContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListPodsTool()


class TestKubernetesGetPodLogsContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesGetPodLogsTool()


class TestKubernetesListDeploymentsContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListDeploymentsTool()


class TestKubernetesGetEventsContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesGetEventsTool()


class TestKubernetesDescribePodContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesDescribePodTool()


class TestKubernetesListNodesContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListNodesTool()


class TestKubernetesListServicesContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListServicesTool()


class TestKubernetesListStatefulSetsContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListStatefulSetsTool()


class TestKubernetesListDaemonSetsContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListDaemonSetsTool()


class TestKubernetesListIngressesContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListIngressesTool()


class TestKubernetesListConfigMapsContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListConfigMapsTool()


class TestKubernetesGetResourceContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesGetResourceTool()


class TestKubernetesListWorkloadsContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListWorkloadsTool()


class TestKubernetesListRolloutsContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListRolloutsTool()


# ---------------------------------------------------------------------------
# is_available / extract_params
# ---------------------------------------------------------------------------


def test_list_pods_is_available_requires_kubeconfig() -> None:
    tool = KubernetesListPodsTool()
    assert tool.is_available({"kubernetes": _K8S_SOURCE}) is True
    assert tool.is_available({}) is False
    assert tool.is_available({"kubernetes": {}}) is False
    assert tool.is_available({"kubernetes": {"kubeconfig": ""}}) is False


def test_list_pods_extract_params_maps_fields() -> None:
    tool = KubernetesListPodsTool()
    sources = mock_agent_state()
    params = tool.extract_params(sources)
    assert params["kubeconfig"] == sources["kubernetes"]["kubeconfig"]
    assert params["default_namespace"] == "default"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_mock_pod(name: str, phase: str = "Running") -> MagicMock:
    pod = MagicMock()
    pod.metadata.name = name
    pod.metadata.namespace = "default"
    pod.metadata.labels = {"app": "test"}
    pod.metadata.creation_timestamp = None
    pod.status.phase = phase
    pod.status.conditions = []
    pod.status.container_statuses = []
    pod.spec.node_name = "node-1"
    return pod


def _make_client_with_core(mock_core: MagicMock) -> Any:
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    client = KubernetesClient(cfg)
    client._core_v1 = mock_core
    client._apps_v1 = MagicMock()
    client._networking_v1 = MagicMock()
    return client


def _make_client_with_apps(mock_apps: MagicMock) -> Any:
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    client = KubernetesClient(cfg)
    client._core_v1 = MagicMock()
    client._apps_v1 = mock_apps
    client._networking_v1 = MagicMock()
    return client


def _make_client_with_networking(mock_networking: MagicMock) -> Any:
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    client = KubernetesClient(cfg)
    client._core_v1 = MagicMock()
    client._apps_v1 = MagicMock()
    client._networking_v1 = mock_networking
    return client


def _make_client_with_apis(
    *,
    core: Any = None,
    apps: Any = None,
    networking: Any = None,
    custom: Any = None,
    batch: Any = None,
) -> Any:
    """KubernetesClient with each API pre-seeded so no kubeconfig is loaded."""
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    client = KubernetesClient(cfg)
    client._core_v1 = core or MagicMock()
    client._apps_v1 = apps or MagicMock()
    client._networking_v1 = networking or MagicMock()
    client._custom_objects = custom or MagicMock()
    client._batch_v1 = batch or MagicMock()
    return client


# ---------------------------------------------------------------------------
# list_pods run()
# ---------------------------------------------------------------------------


def test_list_pods_run_happy_path(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mock_pod_list = MagicMock()
    mock_pod_list.items = [_make_mock_pod("web-abc"), _make_mock_pod("web-xyz")]

    mock_core = MagicMock()
    mock_core.list_namespaced_pod.return_value = mock_pod_list

    tool = KubernetesListPodsTool()

    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is True
    assert result["total"] == 2
    assert result["pods"][0]["name"] == "web-abc"


def test_list_pods_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesListPodsTool()
    result = tool.run(kubeconfig="", namespace="default")
    assert result["available"] is False
    assert result["total"] == 0


def test_list_pods_run_returns_unavailable_on_api_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from kubernetes.client.exceptions import ApiException

    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    mock_client = KubernetesClient(cfg)
    mock_core = MagicMock()
    mock_core.list_namespaced_pod.side_effect = ApiException(status=403, reason="Forbidden")
    mock_client._core_v1 = mock_core
    mock_client._apps_v1 = MagicMock()
    mock_client._networking_v1 = MagicMock()

    tool = KubernetesListPodsTool()
    with patch("integrations.kubernetes.tools._make_client", return_value=mock_client):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is False
    assert "403" in result["error"]


# ---------------------------------------------------------------------------
# get_pod_logs run()
# ---------------------------------------------------------------------------


def test_get_pod_logs_run_happy_path(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    mock_client = KubernetesClient(cfg)
    mock_core = MagicMock()
    mock_core.read_namespaced_pod_log.return_value = "line1\nline2\nline3"
    mock_client._core_v1 = mock_core
    mock_client._apps_v1 = MagicMock()
    mock_client._networking_v1 = MagicMock()

    tool = KubernetesGetPodLogsTool()
    with patch("integrations.kubernetes.tools._make_client", return_value=mock_client):
        result = tool.run(
            kubeconfig=_MINIMAL_KUBECONFIG,
            pod_name="web-abc",
            namespace="default",
        )

    assert result["available"] is True
    assert result["total"] == 3
    assert result["lines"] == ["line1", "line2", "line3"]
    assert result["pod_name"] == "web-abc"


def test_get_pod_logs_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesGetPodLogsTool()
    result = tool.run(kubeconfig="", pod_name="web-abc")
    assert result["available"] is False
    assert result["total"] == 0


def test_get_pod_logs_404_points_the_model_at_fleet_search(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A guessed pod name failing must not just repeat the bare 404.

    Live incident: the model guessed 5 pod names against a single cluster,
    got 5 bare 404s, and never called kubernetes_search_fleet — nothing in
    the error pointed it there. The hint has to name the tool, in the
    observation the model actually reads.
    """
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    mock_client = KubernetesClient(cfg)
    mock_core = MagicMock()
    mock_core.read_namespaced_pod_log.side_effect = ApiException(status=404, reason="Not Found")
    mock_client._core_v1 = mock_core
    mock_client._apps_v1 = MagicMock()
    mock_client._networking_v1 = MagicMock()

    tool = KubernetesGetPodLogsTool()
    with patch("integrations.kubernetes.tools._make_client", return_value=mock_client):
        result = tool.run(
            kubeconfig=_MINIMAL_KUBECONFIG,
            pod_name="orphan-detection-job",
            namespace="default",
        )

    assert result["available"] is False
    assert "kubernetes_search_fleet" in result["error"]


def test_get_pod_logs_403_does_not_get_the_fleet_search_hint(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The hint is specific to "not found" — a permission denial is a different problem."""
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    mock_client = KubernetesClient(cfg)
    mock_core = MagicMock()
    mock_core.read_namespaced_pod_log.side_effect = ApiException(status=403, reason="Forbidden")
    mock_client._core_v1 = mock_core
    mock_client._apps_v1 = MagicMock()
    mock_client._networking_v1 = MagicMock()

    tool = KubernetesGetPodLogsTool()
    with patch("integrations.kubernetes.tools._make_client", return_value=mock_client):
        result = tool.run(
            kubeconfig=_MINIMAL_KUBECONFIG,
            pod_name="web-abc",
            namespace="default",
        )

    assert result["available"] is False
    assert "kubernetes_search_fleet" not in result["error"]


# ---------------------------------------------------------------------------
# list_deployments run()
# ---------------------------------------------------------------------------


def _make_mock_deployment(name: str, desired: int = 3, ready: int = 3) -> MagicMock:
    dep = MagicMock()
    dep.metadata.name = name
    dep.metadata.namespace = "default"
    dep.metadata.labels = {}
    dep.metadata.creation_timestamp = None
    dep.spec.replicas = desired
    dep.status.ready_replicas = ready
    dep.status.available_replicas = ready
    dep.status.unavailable_replicas = desired - ready
    dep.status.updated_replicas = ready
    return dep


def test_list_deployments_run_happy_path(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    mock_client = KubernetesClient(cfg)
    mock_apps = MagicMock()
    mock_dep_list = MagicMock()
    mock_dep_list.items = [_make_mock_deployment("api", desired=3, ready=2)]
    mock_apps.list_namespaced_deployment.return_value = mock_dep_list
    mock_client._core_v1 = MagicMock()
    mock_client._apps_v1 = mock_apps
    mock_client._networking_v1 = MagicMock()

    tool = KubernetesListDeploymentsTool()
    with patch("integrations.kubernetes.tools._make_client", return_value=mock_client):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is True
    assert result["total"] == 1
    assert result["deployments"][0]["name"] == "api"
    assert result["deployments"][0]["unavailable"] == 1


# ---------------------------------------------------------------------------
# get_events run()
# ---------------------------------------------------------------------------


def _make_mock_event(name: str, reason: str = "CrashLoopBackOff") -> MagicMock:
    ev = MagicMock()
    ev.metadata.name = name
    ev.metadata.namespace = "default"
    ev.reason = reason
    ev.message = f"Back-off restarting failed container: {reason}"
    ev.type = "Warning"
    ev.count = 5
    ev.involved_object.kind = "Pod"
    ev.involved_object.name = "web-abc"
    ev.involved_object.namespace = "default"
    ev.first_timestamp = None
    ev.last_timestamp = None
    return ev


def test_get_events_run_happy_path(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    mock_client = KubernetesClient(cfg)
    mock_core = MagicMock()
    mock_ev_list = MagicMock()
    mock_ev_list.items = [_make_mock_event("ev-1")]
    mock_core.list_namespaced_event.return_value = mock_ev_list
    mock_client._core_v1 = mock_core
    mock_client._apps_v1 = MagicMock()
    mock_client._networking_v1 = MagicMock()

    tool = KubernetesGetEventsTool()
    with patch("integrations.kubernetes.tools._make_client", return_value=mock_client):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is True
    assert result["total"] == 1
    assert result["events"][0]["reason"] == "CrashLoopBackOff"
    assert result["events"][0]["type"] == "Warning"


# ---------------------------------------------------------------------------
# describe_pod run()
# ---------------------------------------------------------------------------


def _make_mock_pod_detail(name: str) -> MagicMock:
    pod = MagicMock()
    pod.metadata.name = name
    pod.metadata.namespace = "default"
    pod.metadata.labels = {"app": "web"}
    pod.metadata.annotations = {}
    pod.metadata.creation_timestamp = None
    pod.metadata.owner_references = []
    pod.spec.node_name = "node-1"
    pod.spec.service_account_name = "default"
    pod.spec.node_selector = {}
    pod.spec.volumes = []
    pod.spec.init_containers = []
    c = MagicMock()
    c.name = "app"
    c.image = "nginx:1.25"
    c.ports = []
    c.resources.requests = {"cpu": "100m"}
    c.resources.limits = {"memory": "256Mi"}
    c.env = []
    pod.spec.containers = [c]
    pod.status.phase = "Running"
    pod.status.host_ip = "10.0.0.1"
    pod.status.pod_ip = "192.168.1.5"
    pod.status.conditions = []
    pod.status.container_statuses = []
    pod.status.init_container_statuses = []
    return pod


def test_describe_pod_run_happy_path() -> None:
    mock_core = MagicMock()
    mock_core.read_namespaced_pod.return_value = _make_mock_pod_detail("web-abc")

    tool = KubernetesDescribePodTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, pod_name="web-abc", namespace="default")

    assert result["available"] is True
    assert result["name"] == "web-abc"
    assert result["spec"]["node_name"] == "node-1"
    assert result["status"]["phase"] == "Running"
    assert result["spec"]["containers"][0]["image"] == "nginx:1.25"


def test_describe_pod_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesDescribePodTool()
    result = tool.run(kubeconfig="", pod_name="web-abc")
    assert result["available"] is False


def test_describe_pod_run_includes_valuefrom_env_names_without_values() -> None:
    literal_env = MagicMock()
    literal_env.name = "LOG_LEVEL"
    literal_env.value = "debug"
    secret_env = MagicMock()
    secret_env.name = "DB_PASSWORD"
    secret_env.value = None

    pod = _make_mock_pod_detail("web-abc")
    pod.spec.containers[0].env = [literal_env, secret_env]

    mock_core = MagicMock()
    mock_core.read_namespaced_pod.return_value = pod

    tool = KubernetesDescribePodTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, pod_name="web-abc", namespace="default")

    env_names = result["spec"]["containers"][0]["env"]
    assert env_names == ["LOG_LEVEL", "DB_PASSWORD"]
    assert "debug" not in env_names


def test_describe_pod_run_strips_last_applied_config_annotation() -> None:
    pod = _make_mock_pod_detail("web-abc")
    pod.metadata.annotations = {
        "kubectl.kubernetes.io/last-applied-configuration": (
            '{"spec":{"containers":[{"env":[{"name":"DB_PASSWORD","value":"hunter2"}]}]}}'
        ),
        "some-other/annotation": "keep-me",
    }

    mock_core = MagicMock()
    mock_core.read_namespaced_pod.return_value = pod

    tool = KubernetesDescribePodTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, pod_name="web-abc", namespace="default")

    assert "kubectl.kubernetes.io/last-applied-configuration" not in result["annotations"]
    assert result["annotations"]["some-other/annotation"] == "keep-me"


# ---------------------------------------------------------------------------
# list_nodes run()
# ---------------------------------------------------------------------------


def _make_mock_node(name: str, ready: bool = True) -> MagicMock:
    node = MagicMock()
    node.metadata.name = name
    node.metadata.labels = {"kubernetes.io/hostname": name}
    node.metadata.creation_timestamp = None
    node.spec.taints = []
    cond = MagicMock()
    cond.type = "Ready"
    cond.status = "True" if ready else "False"
    cond.reason = "KubeletReady"
    cond.message = "kubelet is posting ready status"
    node.status.conditions = [cond]
    node.status.capacity = {"cpu": "4", "memory": "8Gi"}
    node.status.allocatable = {"cpu": "3900m", "memory": "7Gi"}
    return node


def test_list_nodes_run_happy_path() -> None:
    mock_core = MagicMock()
    mock_node_list = MagicMock()
    mock_node_list.items = [_make_mock_node("node-1"), _make_mock_node("node-2", ready=False)]
    mock_core.list_node.return_value = mock_node_list

    tool = KubernetesListNodesTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG)

    assert result["available"] is True
    assert result["total"] == 2
    assert result["nodes"][0]["name"] == "node-1"
    assert result["nodes"][0]["conditions"][0]["type"] == "Ready"
    assert result["nodes"][0]["conditions"][0]["status"] == "True"
    assert result["nodes"][1]["conditions"][0]["status"] == "False"


def test_list_nodes_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesListNodesTool()
    result = tool.run(kubeconfig="")
    assert result["available"] is False
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# list_services run()
# ---------------------------------------------------------------------------


def _make_mock_service(name: str, svc_type: str = "ClusterIP") -> MagicMock:
    svc = MagicMock()
    svc.metadata.name = name
    svc.metadata.namespace = "default"
    svc.metadata.labels = {}
    svc.metadata.creation_timestamp = None
    svc.spec.type = svc_type
    svc.spec.cluster_ip = "10.96.0.1"
    svc.spec.external_i_ps = []
    svc.spec.selector = {"app": name}
    port = MagicMock()
    port.name = "http"
    port.port = 80
    port.target_port = 8080
    port.protocol = "TCP"
    port.node_port = None
    svc.spec.ports = [port]
    return svc


def test_list_services_run_happy_path() -> None:
    mock_core = MagicMock()
    mock_svc_list = MagicMock()
    mock_svc_list.items = [_make_mock_service("api"), _make_mock_service("frontend")]
    mock_core.list_namespaced_service.return_value = mock_svc_list

    tool = KubernetesListServicesTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is True
    assert result["total"] == 2
    assert result["services"][0]["name"] == "api"
    assert result["services"][0]["type"] == "ClusterIP"
    assert result["services"][0]["ports"][0]["port"] == 80


def test_list_services_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesListServicesTool()
    result = tool.run(kubeconfig="", namespace="default")
    assert result["available"] is False
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# list_statefulsets run()
# ---------------------------------------------------------------------------


def _make_mock_statefulset(name: str, desired: int = 3, ready: int = 3) -> MagicMock:
    sts = MagicMock()
    sts.metadata.name = name
    sts.metadata.namespace = "default"
    sts.metadata.labels = {}
    sts.metadata.creation_timestamp = None
    sts.spec.replicas = desired
    sts.status.ready_replicas = ready
    sts.status.current_replicas = ready
    sts.status.updated_replicas = ready
    return sts


def test_list_statefulsets_run_happy_path() -> None:
    mock_apps = MagicMock()
    mock_sts_list = MagicMock()
    mock_sts_list.items = [_make_mock_statefulset("postgres", desired=3, ready=2)]
    mock_apps.list_namespaced_stateful_set.return_value = mock_sts_list

    tool = KubernetesListStatefulSetsTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_apps(mock_apps),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is True
    assert result["total"] == 1
    assert result["statefulsets"][0]["name"] == "postgres"
    assert result["statefulsets"][0]["desired"] == 3
    assert result["statefulsets"][0]["ready"] == 2


def test_list_statefulsets_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesListStatefulSetsTool()
    result = tool.run(kubeconfig="", namespace="default")
    assert result["available"] is False
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# list_daemonsets run()
# ---------------------------------------------------------------------------


def _make_mock_daemonset(name: str, desired: int = 5, ready: int = 5) -> MagicMock:
    ds = MagicMock()
    ds.metadata.name = name
    ds.metadata.namespace = "default"
    ds.metadata.labels = {}
    ds.metadata.creation_timestamp = None
    ds.status.desired_number_scheduled = desired
    ds.status.current_number_scheduled = desired
    ds.status.number_ready = ready
    ds.status.updated_number_scheduled = ready
    ds.status.number_available = ready
    return ds


def test_list_daemonsets_run_happy_path() -> None:
    mock_apps = MagicMock()
    mock_ds_list = MagicMock()
    mock_ds_list.items = [_make_mock_daemonset("fluentd", desired=5, ready=4)]
    mock_apps.list_namespaced_daemon_set.return_value = mock_ds_list

    tool = KubernetesListDaemonSetsTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_apps(mock_apps),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is True
    assert result["total"] == 1
    assert result["daemonsets"][0]["name"] == "fluentd"
    assert result["daemonsets"][0]["desired"] == 5
    assert result["daemonsets"][0]["ready"] == 4


def test_list_daemonsets_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesListDaemonSetsTool()
    result = tool.run(kubeconfig="", namespace="default")
    assert result["available"] is False
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# list_ingresses run()
# ---------------------------------------------------------------------------


def _make_mock_ingress(name: str, host: str = "api.example.com") -> MagicMock:
    ing = MagicMock()
    ing.metadata.name = name
    ing.metadata.namespace = "default"
    ing.metadata.labels = {}
    ing.metadata.creation_timestamp = None
    ing.spec.ingress_class_name = "nginx"
    path = MagicMock()
    path.path = "/api"
    path.path_type = "Prefix"
    path.backend.service.name = "api-svc"
    path.backend.service.port.number = 80
    rule = MagicMock()
    rule.host = host
    rule.http.paths = [path]
    ing.spec.rules = [rule]
    ing.spec.tls = []
    ing.status.load_balancer.ingress = []
    return ing


def test_list_ingresses_run_happy_path() -> None:
    mock_networking = MagicMock()
    mock_ing_list = MagicMock()
    mock_ing_list.items = [_make_mock_ingress("api-ingress")]
    mock_networking.list_namespaced_ingress.return_value = mock_ing_list

    tool = KubernetesListIngressesTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_networking(mock_networking),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is True
    assert result["total"] == 1
    assert result["ingresses"][0]["name"] == "api-ingress"
    assert result["ingresses"][0]["ingress_class"] == "nginx"
    assert result["ingresses"][0]["rules"][0]["host"] == "api.example.com"
    assert result["ingresses"][0]["rules"][0]["paths"][0]["service_name"] == "api-svc"


def test_list_ingresses_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesListIngressesTool()
    result = tool.run(kubeconfig="", namespace="default")
    assert result["available"] is False
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# list_configmaps run()
# ---------------------------------------------------------------------------


def _make_mock_configmap(name: str, data: dict[str, str] | None = None) -> MagicMock:
    cm = MagicMock()
    cm.metadata.name = name
    cm.metadata.namespace = "default"
    cm.metadata.labels = {}
    cm.metadata.creation_timestamp = None
    cm.data = data or {"key1": "value1", "key2": "value2"}
    return cm


def test_list_configmaps_run_happy_path() -> None:
    mock_core = MagicMock()
    mock_cm_list = MagicMock()
    mock_cm_list.items = [
        _make_mock_configmap("app-config", {"DB_HOST": "postgres:5432", "LOG_LEVEL": "info"})
    ]
    mock_core.list_namespaced_config_map.return_value = mock_cm_list

    tool = KubernetesListConfigMapsTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is True
    assert result["total"] == 1
    assert result["configmaps"][0]["name"] == "app-config"
    assert result["configmaps"][0]["data"]["DB_HOST"] == "postgres:5432"
    assert "DB_HOST" in result["configmaps"][0]["data_keys"]


def test_list_configmaps_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesListConfigMapsTool()
    result = tool.run(kubeconfig="", namespace="default")
    assert result["available"] is False
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# get_resource run()
# ---------------------------------------------------------------------------


def test_get_resource_run_happy_path() -> None:
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    mock_client = KubernetesClient(cfg)
    mock_core = MagicMock()
    mock_dep = MagicMock()
    mock_apps = MagicMock()
    mock_apps.read_namespaced_deployment.return_value = mock_dep
    mock_client._core_v1 = mock_core
    mock_client._apps_v1 = mock_apps
    mock_client._networking_v1 = MagicMock()
    # simulate sanitize_for_serialization
    mock_client._api_client = MagicMock()
    mock_client._api_client.sanitize_for_serialization.return_value = {
        "kind": "Deployment",
        "metadata": {"name": "api"},
    }

    tool = KubernetesGetResourceTool()
    with patch("integrations.kubernetes.tools._make_client", return_value=mock_client):
        result = tool.run(
            kubeconfig=_MINIMAL_KUBECONFIG,
            resource_type="deployment",
            name="api",
            namespace="default",
        )

    assert result["available"] is True
    assert result["resource_type"] == "deployment"
    assert result["name"] == "api"
    assert result["resource"]["kind"] == "Deployment"


def test_get_resource_run_strips_last_applied_config_annotation() -> None:
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    mock_client = KubernetesClient(cfg)
    mock_client._core_v1 = MagicMock()
    mock_apps = MagicMock()
    mock_apps.read_namespaced_deployment.return_value = MagicMock()
    mock_client._apps_v1 = mock_apps
    mock_client._networking_v1 = MagicMock()
    mock_client._api_client = MagicMock()
    mock_client._api_client.sanitize_for_serialization.return_value = {
        "kind": "Deployment",
        "metadata": {
            "name": "api",
            "annotations": {
                "kubectl.kubernetes.io/last-applied-configuration": (
                    '{"spec":{"template":{"spec":{"containers":'
                    '[{"env":[{"name":"DB_PASSWORD","value":"hunter2"}]}]}}}}'
                ),
                "some-other/annotation": "keep-me",
            },
        },
    }

    tool = KubernetesGetResourceTool()
    with patch("integrations.kubernetes.tools._make_client", return_value=mock_client):
        result = tool.run(
            kubeconfig=_MINIMAL_KUBECONFIG,
            resource_type="deployment",
            name="api",
            namespace="default",
        )

    annotations = result["resource"]["metadata"]["annotations"]
    assert "kubectl.kubernetes.io/last-applied-configuration" not in annotations
    assert annotations["some-other/annotation"] == "keep-me"


def test_get_resource_run_unsupported_type() -> None:
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    mock_client = KubernetesClient(cfg)
    mock_client._core_v1 = MagicMock()
    mock_client._apps_v1 = MagicMock()
    mock_client._networking_v1 = MagicMock()

    tool = KubernetesGetResourceTool()
    with patch("integrations.kubernetes.tools._make_client", return_value=mock_client):
        result = tool.run(
            kubeconfig=_MINIMAL_KUBECONFIG, resource_type="foobar", name="x", namespace="default"
        )

    assert result["available"] is False
    assert "foobar" in result["error"]


def test_get_resource_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesGetResourceTool()
    result = tool.run(kubeconfig="", resource_type="deployment", name="api")
    assert result["available"] is False


# ---------------------------------------------------------------------------
# Multi-cluster selection
# ---------------------------------------------------------------------------


class TestKubernetesListClustersContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListClustersTool()


_MULTI_CLUSTER_SOURCES = {
    "kubernetes": {"kubeconfig": "kc-dev", "context": "ctx-dev", "namespace": "dev"},
    "_all_kubernetes_instances": [
        {
            "name": "gke-dev",
            "tags": {"env": "dev"},
            "config": {"kubeconfig": "kc-dev", "context": "ctx-dev", "namespace": "dev"},
        },
        {
            "name": "gke-prod",
            "tags": {"env": "prod"},
            "config": {"kubeconfig_path": "/p/prod", "context": "ctx-prod", "namespace": "prod"},
        },
    ],
}


def test_extract_params_includes_cluster_configs() -> None:
    tool = KubernetesListPodsTool()
    params = tool.extract_params(_MULTI_CLUSTER_SOURCES)
    # cluster_configs is trusted connection configuration: it MUST be a protected
    # injected param so the runtime re-forces it over any model-supplied value.
    assert "cluster_configs" in tool.injected_params
    assert sorted(params["cluster_configs"]) == ["gke-dev", "gke-prod"]
    assert params["cluster_configs"]["gke-prod"]["kubeconfig_path"] == "/p/prod"


def test_cluster_configs_is_protected_on_every_connection_tool() -> None:
    # Every tool that builds a client from cluster_configs must protect it.
    connection_tools = [
        KubernetesListPodsTool(),
        KubernetesGetPodLogsTool(),
        KubernetesListDeploymentsTool(),
        KubernetesGetEventsTool(),
        KubernetesDescribePodTool(),
        KubernetesListNodesTool(),
        KubernetesListServicesTool(),
        KubernetesListStatefulSetsTool(),
        KubernetesListDaemonSetsTool(),
        KubernetesListIngressesTool(),
        KubernetesListConfigMapsTool(),
        KubernetesGetResourceTool(),
        KubernetesListWorkloadsTool(),
        KubernetesListRolloutsTool(),
    ]
    for tool in connection_tools:
        assert "cluster_configs" in tool.injected_params, tool.name


def test_model_cannot_override_cluster_configs_via_tool_input() -> None:
    """A model-supplied cluster_configs must never replace the trusted map.

    Exercises the real runtime merge (core.execution): because cluster_configs
    is a protected injected param, the extracted (store-derived) map wins, so
    the client is built from the registered cluster's connection fields, not
    the model's.
    """
    from core.execution import execute_tool_calls
    from core.llm.types import ToolCall
    from core.tool_framework.registered_tool import RegisteredTool

    mock_pod_list = MagicMock()
    mock_pod_list.items = []
    mock_core = MagicMock()
    mock_core.list_namespaced_pod.return_value = mock_pod_list

    resolved = {
        "kubernetes": {"kubeconfig": "kc-dev", "context": "ctx-dev", "namespace": "dev"},
        "_all_kubernetes_instances": [
            {"name": "gke-dev", "tags": {}, "config": {"kubeconfig": "kc-dev"}},
            {
                "name": "gke-prod",
                "tags": {},
                "config": {"kubeconfig_path": "/trusted/prod", "namespace": "prod"},
            },
        ],
    }
    malicious_input = {
        "cluster": "gke-prod",
        "cluster_configs": {"gke-prod": {"kubeconfig_path": "/attacker/controlled"}},
    }

    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ) as mock_make:
        execute_tool_calls(
            [ToolCall(id="c1", name="kubernetes_list_pods", input=malicious_input)],
            [RegisteredTool.from_base_tool(KubernetesListPodsTool())],
            resolved,
        )

    built_from = mock_make.call_args.args[0]["kubernetes"]
    assert built_from["kubeconfig_path"] == "/trusted/prod"
    assert built_from["kubeconfig_path"] != "/attacker/controlled"


def test_run_targets_named_cluster() -> None:
    mock_pod_list = MagicMock()
    mock_pod_list.items = [_make_mock_pod("prod-pod")]
    mock_core = MagicMock()
    mock_core.list_namespaced_pod.return_value = mock_pod_list

    cluster_configs = KubernetesListPodsTool().extract_params(_MULTI_CLUSTER_SOURCES)[
        "cluster_configs"
    ]

    tool = KubernetesListPodsTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ) as mock_make:
        result = tool.run(
            kubeconfig="kc-dev",
            context="ctx-dev",
            # The *injected* default-instance namespace. It must not leak into a
            # named cluster; the model-facing argument is ``namespace``.
            default_namespace="dev",
            cluster="gke-prod",
            cluster_configs=cluster_configs,
        )

    # Client built from the prod instance's connection fields, not the injected default.
    assert mock_make.call_args.args[0] == {"kubernetes": cluster_configs["gke-prod"]}
    # Query scoped to the prod instance's namespace.
    assert mock_core.list_namespaced_pod.call_args.kwargs["namespace"] == "prod"
    assert result["available"] is True
    assert result["namespace"] == "prod"
    assert result["total"] == 1


def test_run_omitting_cluster_uses_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mock_pod_list = MagicMock()
    mock_pod_list.items = []
    mock_core = MagicMock()
    mock_core.list_namespaced_pod.return_value = mock_pod_list

    tool = KubernetesListPodsTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ) as mock_make:
        tool.run(kubeconfig="kc-default", context="ctx-a", default_namespace="ns-a")

    # No cluster named -> the injected default connection fields are used verbatim.
    assert mock_make.call_args.args[0] == {
        "kubernetes": {
            "kubeconfig": "kc-default",
            "kubeconfig_path": "",
            "context": "ctx-a",
            "namespace": "ns-a",
        }
    }
    assert mock_core.list_namespaced_pod.call_args.kwargs["namespace"] == "ns-a"


def test_run_unknown_cluster_errors() -> None:
    tool = KubernetesListPodsTool()
    result = tool.run(
        kubeconfig="kc-default",
        cluster="ghost",
        cluster_configs={"gke-dev": {"kubeconfig": "kc-dev"}},
    )
    assert result["available"] is False
    assert "ghost" in result["error"]
    assert "gke-dev" in result["error"]
    assert result["total"] == 0


def test_list_clusters_is_available() -> None:
    tool = KubernetesListClustersTool()
    assert tool.is_available({"kubernetes": _K8S_SOURCE}) is True
    assert tool.is_available({}) is False


def test_list_clusters_run_lists_registered_instances() -> None:
    tool = KubernetesListClustersTool()
    params = tool.extract_params(_MULTI_CLUSTER_SOURCES)
    result = tool.run(**params)
    assert result["available"] is True
    assert result["total"] == 2
    assert [c["name"] for c in result["clusters"]] == ["gke-dev", "gke-prod"]
    assert result["clusters"][0]["is_default"] is True
    assert result["clusters"][1]["is_default"] is False
    assert result["clusters"][1]["tags"] == {"env": "prod"}


def test_list_clusters_run_single_default() -> None:
    tool = KubernetesListClustersTool()
    params = tool.extract_params({"kubernetes": _K8S_SOURCE})
    result = tool.run(**params)
    assert result["total"] == 1
    assert result["clusters"][0]["name"] == "default"
    assert result["clusters"][0]["is_default"] is True


# --- read-only surface -------------------------------------------------------


def test_every_fetchable_resource_is_read_only() -> None:
    """The kubernetes integration reads; it never writes.

    Documented as the reason auto-registering a GKE cluster is an exposure
    decision rather than a destructive one (``docs/gcp.mdx``), and prose is not
    enforceable. If someone adds a ``patch_``/``delete_``/``create_`` entry here,
    that argument stops being true and this fails rather than the docs quietly
    going stale.
    """
    verbs = {entry.method for entry in _RESOURCE_DISPATCH.values()}

    assert verbs, "dispatch table is empty; this test would pass vacuously"
    # Allow both "read_" (typed clients) and "get_" (custom objects API)
    read_only_verbs = [verb for verb in verbs if verb.startswith(("read_", "get_"))]
    assert len(read_only_verbs) == len(verbs), (
        f"Non-read-only verbs found: {sorted(verbs - set(read_only_verbs))}"
    )
    # Explicit negative check for dangerous verbs
    dangerous_verbs = [
        v
        for v in verbs
        if v.startswith(("create_", "patch_", "delete_", "replace_", "post_", "put_"))
    ]
    assert not dangerous_verbs, f"Dangerous verbs found: {sorted(dangerous_verbs)}"


def test_secrets_are_not_fetchable() -> None:
    """``kubernetes_get_resource`` takes an enum, and Secret is deliberately absent.

    Pod logs and configmaps already leak credentials by accident; reading Secrets
    would do it by design.
    """
    assert not [key for key in _RESOURCE_DISPATCH if "secret" in key.lower()]


# --- Namespace targeting tests -----------------------------------------------


def test_model_namespace_argument_survives_the_runtime_merge() -> None:
    """Namespace passed by model reaches the client, not the stored default.

    This test goes through the real execute_tool_calls path rather than calling
    run() directly because it reproduces the bug that _base_params starts
    re-emitting a key named namespace (section 0.4 of the plan). A unit-level
    run() test would miss this since it bypasses the runtime merge.
    """
    # Create a resolved context with a stored namespace different from model's
    resolved = {
        "kubernetes": {
            **_K8S_SOURCE,
            "namespace": "stored-namespace",  # Different from what model will pass
        }
    }

    with patch("integrations.kubernetes.client.KubernetesClient.list_pods") as mock_list:
        mock_list.return_value = {"success": True, "pods": [], "total": 0}

        # Execute with model providing a specific namespace
        execute_tool_calls(
            [
                ToolCall(
                    id="test1", name="kubernetes_list_pods", input={"namespace": "model-namespace"}
                )
            ],
            [RegisteredTool.from_base_tool(KubernetesListPodsTool())],
            resolved,
        )

        # The namespace that reached the client should be the model's, not stored
        mock_list.assert_called_once()
        call_args = mock_list.call_args
        assert call_args.kwargs["namespace"] == "model-namespace"


def test_omitted_namespace_falls_back_to_the_named_clusters_namespace() -> None:
    """With no model namespace, use the named cluster's stored namespace."""
    # Two clusters with different stored namespaces
    multi_cluster_sources = {
        "kubernetes": {**_K8S_SOURCE, "namespace": "default"},
        "_all_kubernetes_instances": [
            {"name": "cluster-a", "config": {**_K8S_SOURCE, "namespace": "namespace-a"}},
            {"name": "cluster-b", "config": {**_K8S_SOURCE, "namespace": "namespace-b"}},
        ],
    }

    tool = KubernetesListPodsTool()
    params = tool.extract_params(multi_cluster_sources)

    with patch("integrations.kubernetes.client.KubernetesClient.list_pods") as mock_list:
        mock_list.return_value = {"success": True, "pods": [], "total": 0}

        # Call with cluster="cluster-b" and no namespace
        tool.run(**params, cluster="cluster-b", namespace="")

        # Should use cluster-b's stored namespace
        mock_list.assert_called_once()
        call_args = mock_list.call_args
        assert call_args.kwargs["namespace"] == "namespace-b"


def test_model_namespace_wins_over_the_named_clusters_namespace() -> None:
    """The bug this branch exists for: both selectors in one call.

    "pods in payments on the prod cluster" carries a cluster *and* a namespace,
    and the prompt fragment tells the model to send both. If naming a cluster
    discards the namespace, every such turn silently queries that cluster's
    configured default — which is ``default`` for every auto-registered GKE
    cluster — and reports "nothing is wrong".
    """
    multi_cluster_sources = {
        "kubernetes": {**_K8S_SOURCE, "namespace": "default"},
        "_all_kubernetes_instances": [
            {"name": "cluster-a", "config": {**_K8S_SOURCE, "namespace": "namespace-a"}},
            {"name": "cluster-b", "config": {**_K8S_SOURCE, "namespace": "namespace-b"}},
        ],
    }

    tool = KubernetesListPodsTool()
    params = tool.extract_params(multi_cluster_sources)

    with patch("integrations.kubernetes.client.KubernetesClient.list_pods") as mock_list:
        mock_list.return_value = {"success": True, "pods": [], "total": 0}

        tool.run(**params, cluster="cluster-b", namespace="payments")

        mock_list.assert_called_once()
        assert mock_list.call_args.kwargs["namespace"] == "payments"


#: Every namespaced tool: its client method, the extra ``run`` arguments it
#: requires, and the payload keys that method returns on success. One entry per
#: tool so a call site that keeps the old ``conn.get("namespace")`` resolution
#: fails here instead of shipping — namespace resolution is duplicated per tool,
#: so pinning only ``list_pods`` leaves ten call sites unguarded.
_NAMESPACED_TOOL_CASES: list[tuple[Any, str, dict[str, Any], dict[str, Any]]] = [
    (KubernetesListPodsTool, "list_pods", {}, {"pods": [], "total": 0}),
    (
        KubernetesGetPodLogsTool,
        "get_pod_logs",
        {"pod_name": "p"},
        {"lines": [], "total": 0, "truncated": False},
    ),
    (KubernetesListDeploymentsTool, "list_deployments", {}, {"deployments": [], "total": 0}),
    (KubernetesGetEventsTool, "get_events", {}, {"events": [], "total": 0}),
    (KubernetesDescribePodTool, "describe_pod", {"pod_name": "p"}, {"spec": {}, "status": {}}),
    (KubernetesListServicesTool, "list_services", {}, {"services": [], "total": 0}),
    (KubernetesListStatefulSetsTool, "list_statefulsets", {}, {"statefulsets": [], "total": 0}),
    (KubernetesListDaemonSetsTool, "list_daemonsets", {}, {"daemonsets": [], "total": 0}),
    (KubernetesListIngressesTool, "list_ingresses", {}, {"ingresses": [], "total": 0}),
    (KubernetesListConfigMapsTool, "list_configmaps", {}, {"configmaps": [], "total": 0}),
    (
        KubernetesGetResourceTool,
        "get_resource",
        {"resource_type": "pod", "name": "p"},
        {"resource": {}, "resource_type": "pod", "name": "p"},
    ),
    (
        KubernetesListWorkloadsTool,
        "list_workloads",
        {},
        {
            "workloads": [],
            "total": 0,
            "truncated": False,
            "truncated_kinds": [],
            "unavailable_kinds": [],
        },
    ),
    (
        KubernetesListRolloutsTool,
        "list_rollouts",
        {},
        {"rollouts": [], "total": 0, "truncated": False},
    ),
]


@pytest.mark.parametrize(
    ("tool_class", "client_method", "extra", "payload"),
    _NAMESPACED_TOOL_CASES,
    ids=[case[1] for case in _NAMESPACED_TOOL_CASES],
)
def test_every_namespaced_tool_honours_the_model_namespace(
    tool_class: Any, client_method: str, extra: dict[str, Any], payload: dict[str, Any]
) -> None:
    """Namespace resolution is per-tool, so pin it on every tool, not just pods."""
    tool = tool_class()
    params = tool.extract_params({"kubernetes": {**_K8S_SOURCE, "namespace": "stored"}})

    with patch(f"integrations.kubernetes.client.KubernetesClient.{client_method}") as mock_call:
        mock_call.return_value = {"success": True, **payload}
        result = tool.run(**params, namespace="model-ns", **extra)

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["namespace"] == "model-ns"
    assert result["available"] is True


def test_namespace_falls_back_to_default_when_nothing_is_configured() -> None:
    """With no model namespace and no stored config, fall back to 'default'."""
    source_no_namespace = {
        "connection_verified": True,
        "kubeconfig": _MINIMAL_KUBECONFIG,
        "context": "",
        # no 'namespace' key at all
    }

    tool = KubernetesListPodsTool()
    params = tool.extract_params({"kubernetes": source_no_namespace})

    with patch("integrations.kubernetes.client.KubernetesClient.list_pods") as mock_list:
        mock_list.return_value = {"success": True, "pods": [], "total": 0}

        # Call with empty/whitespace namespace
        tool.run(**params, namespace="   ")

        # Should fall back to "default"
        mock_list.assert_called_once()
        call_args = mock_list.call_args
        assert call_args.kwargs["namespace"] == "default"


def test_list_namespaces_degrades_when_the_credential_cannot_list_cluster_wide() -> None:
    """list_namespaces degrades gracefully on 403/401."""
    tool = KubernetesListNamespacesTool()
    params = tool.extract_params({"kubernetes": _K8S_SOURCE})

    # Test 403 forbidden case
    with patch("integrations.kubernetes.client.KubernetesClient.list_namespaces") as mock_list:
        mock_list.return_value = {
            "success": False,
            "forbidden": True,
            "error": "namespaces is forbidden",
        }

        result = tool.run(**params)

        assert result["available"] is True
        assert result["listable"] is False
        assert result["namespaces"] == []
        assert result["total"] == 0
        assert "configured_namespace" in result
        assert "note" in result
        assert "cannot enumerate namespaces cluster-wide" in result["note"]

    # Test non-403 error returns tool_unavailable
    with patch("integrations.kubernetes.client.KubernetesClient.list_namespaces") as mock_list:
        mock_list.return_value = {"success": False, "error": "connection failed"}

        result = tool.run(**params)

        assert result["available"] is False
        assert result["listable"] is False


@pytest.mark.parametrize(
    ("status", "expect_forbidden", "expect_reported"),
    [
        (HTTPStatus.FORBIDDEN, True, False),
        (HTTPStatus.UNAUTHORIZED, True, True),
        (HTTPStatus.INTERNAL_SERVER_ERROR, False, True),
    ],
)
def test_list_namespaces_flags_denial_without_filing_an_error_for_403(
    monkeypatch: Any, status: HTTPStatus, expect_forbidden: bool, expect_reported: bool
) -> None:
    """The client — not a hand-fed dict — decides what ``forbidden`` means.

    The tool-level degradation test mocks ``list_namespaces`` wholesale, so it
    cannot see this branch at all. And a 403 is the *expected* answer on a
    namespace-scoped RBAC binding: ``capture_service_error`` grades a non-httpx
    exception ``severity="error"``, so reporting it would file one Sentry error
    per turn forever (the Rootly On-Call precedent).
    """
    reported: list[str] = []

    def _record(exc: BaseException, **kwargs: Any) -> None:
        reported.append(kwargs.get("method", ""))

    monkeypatch.setattr("integrations.kubernetes.client.capture_service_error", _record)

    core = MagicMock()
    core.list_namespace.side_effect = ApiException(status=int(status), reason="denied")
    result = _make_client_with_core(core).list_namespaces()

    assert result["success"] is False
    assert result.get("forbidden", False) is expect_forbidden
    assert bool(reported) is expect_reported


# ---------------------------------------------------------------------------
# New workloads and rollouts tests
# ---------------------------------------------------------------------------


def test_list_workloads_reports_rollouts_alongside_deployments() -> None:
    """Test that list_workloads includes Rollouts alongside Deployments."""
    mock_apps = MagicMock()
    mock_batch = MagicMock()
    mock_custom = MagicMock()

    # Set up deployment response
    deployment = MagicMock()
    deployment.metadata.name = "web-app"
    deployment.metadata.namespace = "default"
    deployment.spec.replicas = 3
    deployment.status.ready_replicas = 3
    deployment.status.updated_replicas = 3
    deployment.status.available_replicas = 3
    deployment.metadata.labels = {"app": "web"}
    deployment.metadata.creation_timestamp = "2023-01-01T00:00:00Z"

    deployment_list = MagicMock()
    deployment_list.items = [deployment]
    deployment_list.metadata._continue = ""
    mock_apps.list_namespaced_deployment.return_value = deployment_list

    # Set up StatefulSet, DaemonSet, CronJob empty responses
    empty_list = MagicMock()
    empty_list.items = []
    empty_list.metadata._continue = ""
    mock_apps.list_namespaced_stateful_set.return_value = empty_list
    mock_apps.list_namespaced_daemon_set.return_value = empty_list
    mock_batch.list_namespaced_cron_job.return_value = empty_list

    # Set up rollout response (CRD returns dict)
    rollout_item = {
        "metadata": {
            "name": "api-service",
            "namespace": "default",
            "labels": {"app": "api"},
            "creationTimestamp": "2023-01-01T00:00:00Z",
        },
        "spec": {"replicas": 2},
        "status": {
            "readyReplicas": 2,
            "updatedReplicas": 2,
            "availableReplicas": 2,
            "phase": "Healthy",
        },
    }
    rollout_response = {"items": [rollout_item], "metadata": {}}
    mock_custom.list_namespaced_custom_object.return_value = rollout_response

    client = _make_client_with_apis(apps=mock_apps, batch=mock_batch, custom=mock_custom)

    result = client.list_workloads(namespace="default", limit=50)

    assert result["success"] is True
    workloads = result["workloads"]

    # Should have both deployment and rollout
    kinds = {w["kind"] for w in workloads}
    assert "Deployment" in kinds
    assert "Rollout" in kinds

    # Check rollout fields survive
    rollout = next(w for w in workloads if w["kind"] == "Rollout")
    assert rollout["name"] == "api-service"
    assert rollout["ready"] == 2
    assert rollout["desired"] == 2
    assert rollout["phase"] == "Healthy"


def test_list_workloads_degrades_when_the_rollouts_crd_is_absent() -> None:
    """Test that list_workloads degrades gracefully when Rollouts CRD is absent."""
    mock_apps = MagicMock()
    mock_batch = MagicMock()
    mock_custom = MagicMock()

    # Set up successful responses for built-in types
    deployment = MagicMock()
    deployment.metadata.name = "app"
    deployment.items = [deployment]
    deployment_list = MagicMock()
    deployment_list.items = [deployment]
    deployment_list.metadata._continue = ""
    mock_apps.list_namespaced_deployment.return_value = deployment_list

    empty_list = MagicMock()
    empty_list.items = []
    empty_list.metadata._continue = ""
    mock_apps.list_namespaced_stateful_set.return_value = empty_list
    mock_apps.list_namespaced_daemon_set.return_value = empty_list
    mock_batch.list_namespaced_cron_job.return_value = empty_list

    # Rollouts CRD is absent (404)
    mock_custom.list_namespaced_custom_object.side_effect = ApiException(
        status=404, reason="Not Found"
    )

    client = _make_client_with_apis(apps=mock_apps, batch=mock_batch, custom=mock_custom)

    result = client.list_workloads(namespace="default", limit=50)

    assert result["success"] is True
    assert len(result["unavailable_kinds"]) == 1
    assert result["unavailable_kinds"][0]["kind"] == "Rollout"
    assert "404" in result["unavailable_kinds"][0]["reason"]

    # Other kinds should still be present
    kinds = {w["kind"] for w in result["workloads"]}
    assert "Deployment" in kinds


def test_list_workloads_files_no_sentry_error_for_an_absent_crd() -> None:
    """Test that absent CRD (404/403) doesn't trigger Sentry error."""
    mock_apps = MagicMock()
    mock_batch = MagicMock()
    mock_custom = MagicMock()

    # Set up empty responses for built-in types
    empty_list = MagicMock()
    empty_list.items = []
    empty_list.metadata._continue = ""
    mock_apps.list_namespaced_deployment.return_value = empty_list
    mock_apps.list_namespaced_stateful_set.return_value = empty_list
    mock_apps.list_namespaced_daemon_set.return_value = empty_list
    mock_batch.list_namespaced_cron_job.return_value = empty_list

    # Record capture_service_error calls
    captured_errors = []

    def _record(exc, **kwargs):
        captured_errors.append((exc.status, kwargs.get("method")))

    with patch("integrations.kubernetes.client.capture_service_error", side_effect=_record):
        client = _make_client_with_apis(apps=mock_apps, batch=mock_batch, custom=mock_custom)

        # Test each status code
        for status, should_report in [(404, False), (403, False), (401, True), (500, True)]:
            captured_errors.clear()
            mock_custom.list_namespaced_custom_object.side_effect = ApiException(status=status)

            client.list_workloads(namespace="default", limit=50)

            if should_report:
                assert len(captured_errors) > 0, f"Status {status} should trigger error reporting"
            else:
                assert len(captured_errors) == 0, (
                    f"Status {status} should not trigger error reporting"
                )


def test_list_workloads_reports_truncation_honestly() -> None:
    """Test that list_workloads reports truncation correctly."""
    mock_apps = MagicMock()
    mock_batch = MagicMock()
    mock_custom = MagicMock()

    # Deployment has truncation
    deployment_list = MagicMock()
    deployment_list.items = []
    deployment_list.metadata._continue = "token123"  # Truncated
    mock_apps.list_namespaced_deployment.return_value = deployment_list

    # Others are not truncated
    empty_list = MagicMock()
    empty_list.items = []
    empty_list.metadata._continue = ""  # Not truncated
    mock_apps.list_namespaced_stateful_set.return_value = empty_list
    mock_apps.list_namespaced_daemon_set.return_value = empty_list
    mock_batch.list_namespaced_cron_job.return_value = empty_list
    mock_custom.list_namespaced_custom_object.return_value = {"items": [], "metadata": {}}

    client = _make_client_with_apis(apps=mock_apps, batch=mock_batch, custom=mock_custom)

    result = client.list_workloads(namespace="default", limit=50)

    assert result["truncated"] is True
    assert result["truncated_kinds"] == ["Deployment"]

    # Test negative case - all empty
    deployment_list.metadata._continue = ""
    result = client.list_workloads(namespace="default", limit=50)
    assert result["truncated"] is False
    assert result["truncated_kinds"] == []


def test_new_listers_keep_credentials_injected_and_selectors_model_facing() -> None:
    """Test that new tools properly protect credentials and expose selectors."""
    # Test both new tools
    tools = [KubernetesListWorkloadsTool(), KubernetesListRolloutsTool()]

    for tool in tools:
        # Credentials must be injected (protected from model)
        assert "cluster_configs" in tool.injected_params

        # Selectors must NOT be injected (model-facing)
        assert "namespace" not in tool.injected_params
        assert "cluster" not in tool.injected_params

        # Selectors must be in input schema (model can set them)
        properties = tool.input_schema.get("properties", {})
        assert "namespace" in properties
        assert "cluster" in properties


def test_model_cannot_override_cluster_configs_on_list_workloads() -> None:
    """Test that model cannot override cluster_configs via malicious input."""
    from core.execution import execute_tool_calls
    from core.llm.types import ToolCall
    from core.tool_framework.registered_tool import RegisteredTool

    # Exactly like test_model_cannot_override_cluster_configs_via_tool_input
    resolved = {
        "kubernetes": {"kubeconfig_path": "/trusted/config", "context": "trusted-context"},
        "_all_kubernetes_instances": [
            {"name": "default", "tags": {}, "config": {"kubeconfig_path": "/trusted/config"}},
        ],
    }
    malicious_input = {
        "cluster_configs": {
            "evil": {
                "kubeconfig_path": "/evil/config",
                "context": "evil-context",
            }
        },
        "namespace": "default",
    }

    with patch("integrations.kubernetes.tools._make_client") as mock_make:
        mock_client = MagicMock()
        mock_client.list_workloads.return_value = {"success": True, "workloads": [], "total": 0}
        mock_make.return_value = mock_client

        execute_tool_calls(
            [ToolCall(id="c1", name="kubernetes_list_workloads", input=malicious_input)],
            [RegisteredTool.from_base_tool(KubernetesListWorkloadsTool())],
            resolved,
        )

        # Should have been called with trusted config, not evil config
        call_args = mock_make.call_args[0][0]
        k8s_config = call_args["kubernetes"]
        assert k8s_config["kubeconfig_path"] == "/trusted/config"
        assert k8s_config["context"] == "trusted-context"


def test_get_resource_fetches_a_rollout_with_env_values_redacted() -> None:
    """Test that get_resource redacts env values from Rollout CRDs."""
    mock_custom = MagicMock()

    # Rollout with env values in the pod template
    rollout_data = {
        "metadata": {
            "name": "test-rollout",
            "annotations": {
                "kubectl.kubernetes.io/last-applied-configuration": '{"secret": "data"}',
                "other-annotation": "keep-this",
            },
        },
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "app",
                            "env": [
                                {"name": "DB_URL", "value": "postgres://u:p@h/db"},
                                {"name": "API_KEY", "value": "secret123"},
                            ],
                        }
                    ]
                }
            }
        },
    }

    mock_custom.get_namespaced_custom_object.return_value = rollout_data

    client = _make_client_with_apis(custom=mock_custom)

    result = client.get_resource(resource_type="rollout", namespace="default", name="test-rollout")

    assert result["success"] is True
    resource = result["resource"]

    # Env names should be present but values removed
    containers = resource["spec"]["template"]["spec"]["containers"]
    env_entries = containers[0]["env"]
    assert len(env_entries) == 2

    for env_entry in env_entries:
        assert "name" in env_entry  # Name kept
        assert "value" not in env_entry  # Value removed
        assert env_entry["name"] in ["DB_URL", "API_KEY"]

    # last-applied-configuration annotation should be removed
    annotations = resource["metadata"]["annotations"]
    assert "kubectl.kubernetes.io/last-applied-configuration" not in annotations
    assert annotations["other-annotation"] == "keep-this"


def test_list_rollouts_surfaces_a_degraded_rollout() -> None:
    """Test that list_rollouts correctly identifies a degraded rollout awaiting promotion."""
    mock_custom = MagicMock()

    # Rollout that is progressing, paused, with different current/stable revisions
    rollout_item = {
        "metadata": {
            "name": "test-rollout",
            "namespace": "default",
            "labels": {"app": "test"},
            "creationTimestamp": "2023-01-01T00:00:00Z",
        },
        "spec": {
            "replicas": 2,
            "paused": True,
            "strategy": {"canary": {"steps": [{"setWeight": 20}, {"pause": {}}]}},
        },
        "status": {
            "phase": "Progressing",
            "replicas": 2,
            "readyReplicas": 1,
            "updatedReplicas": 1,
            "availableReplicas": 1,
            "currentStepIndex": 1,
            "currentPodHash": "new-hash-123",
            "stableRS": "old-hash-456",
            "pauseConditions": [{"reason": "BlueGreenPause", "startTime": "2023-01-01T00:05:00Z"}],
        },
    }

    rollout_response = {"items": [rollout_item], "metadata": {}}
    mock_custom.list_namespaced_custom_object.return_value = rollout_response

    client = _make_client_with_apis(custom=mock_custom)

    result = client.list_rollouts(namespace="default", limit=50)

    assert result["success"] is True
    rollouts = result["rollouts"]
    assert len(rollouts) == 1

    rollout = rollouts[0]
    assert rollout["phase"] == "Progressing"
    assert rollout["ready"] == 1
    assert rollout["desired"] == 2
    assert rollout["awaiting_promotion"] is True  # paused=True + different revisions
    assert rollout["strategy"] == "canary"
    assert rollout["total_steps"] == 2
    assert rollout["pause_reasons"] == ["BlueGreenPause"]


def test_a_paused_rollout_on_the_stable_revision_is_not_awaiting_promotion() -> None:
    """``paused`` alone must not read as "a new revision is waiting for you".

    The positive case above sets both ``spec.paused`` and a differing revision
    pair, so it cannot tell the two clauses apart. A blue-green Rollout that is
    paused *on the revision already serving traffic* has nothing to promote —
    reporting otherwise sends an operator to promote a no-op during an incident.
    Pause here comes only from ``status.pauseConditions`` so that path is
    exercised too.
    """
    mock_custom = MagicMock()
    mock_custom.list_namespaced_custom_object.return_value = {
        "items": [
            {
                "metadata": {"name": "bg", "namespace": "default"},
                "spec": {"replicas": 2, "strategy": {"blueGreen": {"activeService": "svc"}}},
                "status": {
                    "phase": "Paused",
                    "currentPodHash": "same-hash",
                    "stableRS": "same-hash",
                    "pauseConditions": [{"reason": "BlueGreenPause"}],
                },
            }
        ],
        "metadata": {},
    }

    result = _make_client_with_apis(custom=mock_custom).list_rollouts(namespace="default")

    rollout = result["rollouts"][0]
    assert rollout["paused"] is True, "status.pauseConditions must drive paused"
    assert rollout["awaiting_promotion"] is False
    assert rollout["strategy"] == "blueGreen"


@pytest.mark.parametrize(
    ("status", "expect_reported"),
    [
        (HTTPStatus.NOT_FOUND, False),
        (HTTPStatus.FORBIDDEN, False),
        (HTTPStatus.UNAUTHORIZED, True),
        (HTTPStatus.INTERNAL_SERVER_ERROR, True),
    ],
)
def test_list_rollouts_flags_an_absent_crd_without_filing_an_error(
    status: HTTPStatus, expect_reported: bool
) -> None:
    """A cluster that does not run Argo is expected, not an incident.

    404/403 must degrade to ``kind_unavailable`` and skip telemetry, or every
    turn against a non-Argo cluster files one Sentry error forever. 401 is a
    real credential failure and keeps both the generic error and the report.
    """
    mock_custom = MagicMock()
    mock_custom.list_namespaced_custom_object.side_effect = ApiException(
        status=int(status), reason="boom"
    )
    reported: list[Any] = []

    with patch(
        "integrations.kubernetes.client.capture_service_error",
        side_effect=lambda exc, **_kw: reported.append(exc),
    ):
        result = _make_client_with_apis(custom=mock_custom).list_rollouts(namespace="default")

    assert result["success"] is False
    assert result.get("kind_unavailable", False) is not expect_reported
    assert bool(reported) is expect_reported


def test_rollouts_tool_says_the_crd_is_absent_rather_than_failing() -> None:
    """The absent-CRD answer must reach the model as "not checked", not "error"."""
    tool = KubernetesListRolloutsTool()
    client = MagicMock()
    client.__enter__.return_value = client
    client.list_rollouts.return_value = {
        "success": False,
        "error": "Kubernetes API error 404: Not Found",
        "kind_unavailable": True,
    }

    with patch("integrations.kubernetes.tools._make_client", return_value=client):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is True
    assert result["crd_installed"] is False
    assert result["rollouts"] == []
    assert "not evidence the workload is missing" in result["note"]


def test_workloads_tool_forwards_unavailable_kinds_to_the_model() -> None:
    """An unreadable kind must stay visible at the tool boundary.

    The tool description promises "an empty result for a kind means 'none
    exist', not 'not checked'". Dropping ``unavailable_kinds`` on the way out
    turns a namespace whose Rollouts are unreadable into a confident "no such
    workload" — the exact production failure this slice exists to fix.
    """
    tool = KubernetesListWorkloadsTool()
    unavailable = [{"kind": "Rollout", "reason": "Kubernetes API error 403: Forbidden"}]
    client = MagicMock()
    client.__enter__.return_value = client
    client.list_workloads.return_value = {
        "success": True,
        "workloads": [],
        "total": 0,
        "truncated": False,
        "truncated_kinds": [],
        "unavailable_kinds": unavailable,
    }

    with patch("integrations.kubernetes.tools._make_client", return_value=client):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["unavailable_kinds"] == unavailable


def test_list_workloads_reports_cronjobs_without_their_env_values() -> None:
    """CronJobs are one of the five advertised kinds, and carry credentials.

    ``_redact_env_values`` does not walk ``spec.jobTemplate.spec.template``, so
    the only thing keeping a CronJob's env out of the payload is that the row
    projection is an allowlist. Real SDK models are used rather than MagicMock
    because a Mock answers every attribute and would hide both facts.
    """
    from kubernetes import client as k8s

    template = k8s.V1PodTemplateSpec(
        metadata=k8s.V1ObjectMeta(name="t"),
        spec=k8s.V1PodSpec(
            containers=[
                k8s.V1Container(
                    name="c", env=[k8s.V1EnvVar(name="DB_URL", value="postgres://u:pw@h/db")]
                )
            ]
        ),
    )
    cron_jobs = k8s.V1CronJobList(
        items=[
            k8s.V1CronJob(
                metadata=k8s.V1ObjectMeta(name="nightly", namespace="default"),
                spec=k8s.V1CronJobSpec(
                    schedule="0 0 * * *",
                    suspend=False,
                    job_template=k8s.V1JobTemplateSpec(spec=k8s.V1JobSpec(template=template)),
                ),
            )
        ],
        metadata=k8s.V1ListMeta(),
    )

    mock_apps = MagicMock()
    empty = MagicMock()
    empty.items = []
    empty.metadata._continue = ""
    mock_apps.list_namespaced_deployment.return_value = empty
    mock_apps.list_namespaced_stateful_set.return_value = empty
    mock_apps.list_namespaced_daemon_set.return_value = empty
    mock_batch = MagicMock()
    mock_batch.list_namespaced_cron_job.return_value = cron_jobs
    mock_custom = MagicMock()
    mock_custom.list_namespaced_custom_object.return_value = {"items": [], "metadata": {}}

    result = _make_client_with_apis(
        apps=mock_apps, batch=mock_batch, custom=mock_custom
    ).list_workloads(namespace="default")

    rows = result["workloads"]
    assert [row["kind"] for row in rows] == ["CronJob"]
    assert rows[0]["name"] == "nightly"
    assert rows[0]["phase"] == "Active"
    assert "pw@h" not in repr(result), "CronJob env values must never reach the payload"


@pytest.mark.parametrize(
    "tool_class",
    [KubernetesListNodesTool, KubernetesListClustersTool, KubernetesListNamespacesTool],
)
def test_cluster_scoped_tools_expose_no_namespace_parameter(tool_class: type) -> None:
    """Cluster-scoped tools do not expose namespace parameter."""
    tool = tool_class()
    properties = tool.input_schema.get("properties", {})
    assert "namespace" not in properties


def test_get_pod_logs_caps_a_huge_tail_to_the_char_ceiling() -> None:
    """Pins the tool can return an unbounded payload."""
    from config.constants import KUBERNETES_MAX_LOG_CHARS, KUBERNETES_MAX_LOG_TAIL_LINES

    # Create 5000 lines x 400 chars = 2 MB
    huge_logs = "\n".join([f"line {i:04d}: " + "x" * 390 for i in range(5000)])

    mock_core = MagicMock()
    mock_core.read_namespaced_pod_log.return_value = huge_logs

    tool = KubernetesGetPodLogsTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ):
        result = tool.run(
            kubeconfig=_MINIMAL_KUBECONFIG, pod_name="api-worker-7d9f4b", tail_lines=5000
        )

    assert result["available"] is True

    # Lines should be capped by character limit
    joined_lines_length = sum(len(line) + 1 for line in result["lines"])  # +1 for newline
    assert joined_lines_length <= KUBERNETES_MAX_LOG_CHARS

    # SDK should have been called with clamped tail_lines
    mock_core.read_namespaced_pod_log.assert_called_once_with(
        name="api-worker-7d9f4b", namespace="default", tail_lines=KUBERNETES_MAX_LOG_TAIL_LINES
    )

    # Should keep the newest (last) line
    assert result["lines"][-1] == "line 4999: " + "x" * 390


def test_get_pod_logs_flags_truncation_only_when_it_happened() -> None:
    """Pins the indicator is a constant, so the model can't tell it got a slice."""

    # Small log - should not be truncated
    small_logs = "\n".join(["line 1", "line 2", "line 3"])

    mock_core = MagicMock()
    mock_core.read_namespaced_pod_log.return_value = small_logs

    tool = KubernetesGetPodLogsTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, pod_name="api-worker-7d9f4b")

    assert result["truncated"] is False
    assert "truncation_note" not in result

    # Large log - should be truncated
    huge_logs = "\n".join([f"line {i:04d}: " + "x" * 2000 for i in range(100)])

    mock_core.read_namespaced_pod_log.return_value = huge_logs

    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, pod_name="api-worker-7d9f4b")

    assert result["truncated"] is True
    assert "truncation_note" in result
    assert len(result["truncation_note"]) > 0


def test_get_pod_logs_still_returns_lines_when_the_newest_one_is_enormous() -> None:
    """Pins a budget walk that returns nothing because the newest line overflows.

    One base64 blob or single-line stack trace at the end of the log is enough:
    a newest-first walk that only drops whole lines breaks on the first
    iteration and hands back an empty list, which reads as "the pod logged
    nothing" — the opposite of the truth.
    """
    from config.constants import KUBERNETES_MAX_LOG_CHARS, KUBERNETES_MAX_LOG_LINE_CHARS

    lines = [f"line {i:04d}: something happened" for i in range(50)]
    lines.append("BLOB " + "y" * (KUBERNETES_MAX_LOG_CHARS * 2))

    mock_core = MagicMock()
    mock_core.read_namespaced_pod_log.return_value = "\n".join(lines)

    tool = KubernetesGetPodLogsTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, pod_name="api-worker-7d9f4b")

    # The oversized line costs only itself, so the ordinary lines survive.
    assert len(result["lines"]) > 1
    assert "line 0049: something happened" in result["lines"]
    # And it is clipped rather than dropped — its head is where the useful part is.
    assert result["lines"][-1].startswith("BLOB yyy")
    assert len(result["lines"][-1]) <= KUBERNETES_MAX_LOG_LINE_CHARS
    assert result["truncated"] is True


def test_get_pod_logs_declares_its_tail_ceiling_in_the_schema() -> None:
    """Pins a schema that lets the model ask for a tail the tool then silently clamps."""
    from config.constants import KUBERNETES_MAX_LOG_TAIL_LINES

    schema = KubernetesGetPodLogsTool().input_schema
    assert schema["properties"]["tail_lines"]["maximum"] == KUBERNETES_MAX_LOG_TAIL_LINES


# ---------------------------------------------------------------------------
# Fleet-scope caveat on zero-result workload lookups
#
# Live incident: an investigation checked 1 of 17 registered clusters (the
# default one), found nothing, and reported the workload absent and the blast
# radius clear fleet-wide. gather_prompt.py and action_prompt.py already told
# the model to call kubernetes_search_fleet before concluding absence, and it
# did not. The correction has to be in the tool's own return value.
# ---------------------------------------------------------------------------

_FLEET_SCOPE_CLUSTER_CONFIGS = {
    "gke-dev": {"kubeconfig": "kc-dev"},
    "gke-prod": {"kubeconfig_path": "/p/prod"},
}


@pytest.mark.parametrize(
    ("tool_class", "client_method", "payload"),
    [
        (KubernetesListPodsTool, "list_pods", {"pods": [], "total": 0}),
        (KubernetesListDeploymentsTool, "list_deployments", {"deployments": [], "total": 0}),
        (
            KubernetesListStatefulSetsTool,
            "list_statefulsets",
            {"statefulsets": [], "total": 0},
        ),
        (KubernetesListDaemonSetsTool, "list_daemonsets", {"daemonsets": [], "total": 0}),
        (
            KubernetesListWorkloadsTool,
            "list_workloads",
            {
                "workloads": [],
                "total": 0,
                "truncated": False,
                "truncated_kinds": [],
                "unavailable_kinds": [],
            },
        ),
        (
            KubernetesListRolloutsTool,
            "list_rollouts",
            {"rollouts": [], "total": 0, "truncated": False},
        ),
    ],
    ids=["pods", "deployments", "statefulsets", "daemonsets", "workloads", "rollouts"],
)
def test_empty_result_on_default_cluster_flags_the_unchecked_fleet(
    tool_class: Any, client_method: str, payload: dict[str, Any]
) -> None:
    """All six Tier A tools must carry the caveat, not just kubernetes_list_deployments.

    A prior version of this suite pinned only list_deployments — every other Tier A
    tool's note wiring was completely unpinned and survived deletion under mutation.
    """
    with patch(f"integrations.kubernetes.client.KubernetesClient.{client_method}") as mock_call:
        mock_call.return_value = {"success": True, **payload}
        result = tool_class().run(
            kubeconfig=_MINIMAL_KUBECONFIG, cluster_configs=_FLEET_SCOPE_CLUSTER_CONFIGS
        )

    assert "note" in result
    assert "2" in result["note"]
    assert "kubernetes_search_fleet" in result["note"]
    assert "kubernetes_list_clusters" in result["note"]


def test_named_cluster_suppresses_the_scope_note() -> None:
    tool = KubernetesListDeploymentsTool()
    with patch("integrations.kubernetes.client.KubernetesClient.list_deployments") as mock_list:
        mock_list.return_value = {"success": True, "deployments": [], "total": 0}
        result = tool.run(
            kubeconfig=_MINIMAL_KUBECONFIG,
            cluster="gke-prod",
            cluster_configs=_FLEET_SCOPE_CLUSTER_CONFIGS,
        )

    assert "note" not in result


def test_single_registered_cluster_gets_no_scope_note() -> None:
    tool = KubernetesListDeploymentsTool()
    with patch("integrations.kubernetes.client.KubernetesClient.list_deployments") as mock_list:
        mock_list.return_value = {"success": True, "deployments": [], "total": 0}
        result = tool.run(
            kubeconfig=_MINIMAL_KUBECONFIG,
            cluster_configs={"gke-dev": {"kubeconfig": "kc-dev"}},
        )

    assert "note" not in result


def test_non_empty_result_gets_no_scope_note() -> None:
    tool = KubernetesListDeploymentsTool()
    with patch("integrations.kubernetes.client.KubernetesClient.list_deployments") as mock_list:
        mock_list.return_value = {
            "success": True,
            "deployments": [{"name": "api"}],
            "total": 1,
        }
        result = tool.run(
            kubeconfig=_MINIMAL_KUBECONFIG, cluster_configs=_FLEET_SCOPE_CLUSTER_CONFIGS
        )

    assert "note" not in result


def test_list_workloads_with_every_kind_unreadable_gets_no_scope_note() -> None:
    """Zero workloads because EVERY kind (all 5) was unreadable is a capability
    problem, not a cluster-scope problem — pointing at another cluster here is
    the wrong advice when the payload already reports unavailable_kinds.
    """
    tool = KubernetesListWorkloadsTool()
    unavailable = [
        {"kind": kind, "reason": "Kubernetes API error 403: Forbidden"}
        for kind in ("Deployment", "StatefulSet", "DaemonSet", "CronJob", "Rollout")
    ]
    with patch("integrations.kubernetes.client.KubernetesClient.list_workloads") as mock_list:
        mock_list.return_value = {
            "success": True,
            "workloads": [],
            "total": 0,
            "truncated": False,
            "truncated_kinds": [],
            "unavailable_kinds": unavailable,
        }
        result = tool.run(
            kubeconfig=_MINIMAL_KUBECONFIG, cluster_configs=_FLEET_SCOPE_CLUSTER_CONFIGS
        )

    assert "note" not in result


def test_list_workloads_with_one_missing_crd_still_gets_the_scope_note() -> None:
    """Most clusters in this fleet do not run Argo Rollouts, so a single 404 on
    that one kind is the common case — not "every kind was unreadable". The
    other 4 kinds read cleanly and empty, so the fleet-scope caveat must still
    fire. Suppressing it here was the actual defect: it silently turned this
    fix off on most of the fleet, the exact failure mode it exists to close.
    """
    tool = KubernetesListWorkloadsTool()
    unavailable = [{"kind": "Rollout", "reason": "Kubernetes API error 404: Not Found"}]
    with patch("integrations.kubernetes.client.KubernetesClient.list_workloads") as mock_list:
        mock_list.return_value = {
            "success": True,
            "workloads": [],
            "total": 0,
            "truncated": False,
            "truncated_kinds": [],
            "unavailable_kinds": unavailable,
        }
        result = tool.run(
            kubeconfig=_MINIMAL_KUBECONFIG, cluster_configs=_FLEET_SCOPE_CLUSTER_CONFIGS
        )

    assert "note" in result
    assert "kubernetes_search_fleet" in result["note"]


def test_describe_pod_404_points_at_fleet_search() -> None:
    """Mirrors test_get_pod_logs_404_points_the_model_at_fleet_search for
    kubernetes_describe_pod. Kept separate from get_resource's equivalent test
    so a regression in one tool cannot hide behind the other tool's assert.
    """
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})

    describe_client = KubernetesClient(cfg)
    mock_core = MagicMock()
    mock_core.read_namespaced_pod.side_effect = ApiException(status=404, reason="Not Found")
    describe_client._core_v1 = mock_core
    describe_client._apps_v1 = MagicMock()
    describe_client._networking_v1 = MagicMock()

    with patch("integrations.kubernetes.tools._make_client", return_value=describe_client):
        result = KubernetesDescribePodTool().run(
            kubeconfig=_MINIMAL_KUBECONFIG,
            pod_name="ghost-pod",
            cluster_configs=_FLEET_SCOPE_CLUSTER_CONFIGS,
        )
    assert "kubernetes_search_fleet" in result["error"]


def test_get_resource_404_points_at_fleet_search() -> None:
    """Mirrors test_get_pod_logs_404_points_the_model_at_fleet_search for
    kubernetes_get_resource. Kept separate from describe_pod's equivalent test
    so a regression in one tool cannot hide behind the other tool's assert.
    """
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})

    get_resource_client = KubernetesClient(cfg)
    mock_apps = MagicMock()
    mock_apps.read_namespaced_deployment.side_effect = ApiException(status=404, reason="Not Found")
    get_resource_client._core_v1 = MagicMock()
    get_resource_client._apps_v1 = mock_apps
    get_resource_client._networking_v1 = MagicMock()

    with patch("integrations.kubernetes.tools._make_client", return_value=get_resource_client):
        result = KubernetesGetResourceTool().run(
            kubeconfig=_MINIMAL_KUBECONFIG,
            resource_type="deployment",
            name="ghost-deploy",
            cluster_configs=_FLEET_SCOPE_CLUSTER_CONFIGS,
        )
    assert "kubernetes_search_fleet" in result["error"]


def test_describe_pod_403_does_not_get_the_scope_hint() -> None:
    """The hint is specific to "not found" — a permission denial is a different problem."""
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})

    describe_client = KubernetesClient(cfg)
    mock_core = MagicMock()
    mock_core.read_namespaced_pod.side_effect = ApiException(status=403, reason="Forbidden")
    describe_client._core_v1 = mock_core
    describe_client._apps_v1 = MagicMock()
    describe_client._networking_v1 = MagicMock()

    with patch("integrations.kubernetes.tools._make_client", return_value=describe_client):
        result = KubernetesDescribePodTool().run(
            kubeconfig=_MINIMAL_KUBECONFIG,
            pod_name="web-abc",
            cluster_configs=_FLEET_SCOPE_CLUSTER_CONFIGS,
        )
    assert "kubernetes_search_fleet" not in result["error"]


def test_get_resource_403_does_not_get_the_scope_hint() -> None:
    """The hint is specific to "not found" — a permission denial is a different problem."""
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})

    get_resource_client = KubernetesClient(cfg)
    mock_apps = MagicMock()
    mock_apps.read_namespaced_deployment.side_effect = ApiException(status=403, reason="Forbidden")
    get_resource_client._core_v1 = MagicMock()
    get_resource_client._apps_v1 = mock_apps
    get_resource_client._networking_v1 = MagicMock()

    with patch("integrations.kubernetes.tools._make_client", return_value=get_resource_client):
        result = KubernetesGetResourceTool().run(
            kubeconfig=_MINIMAL_KUBECONFIG,
            resource_type="deployment",
            name="api",
            cluster_configs=_FLEET_SCOPE_CLUSTER_CONFIGS,
        )
    assert "kubernetes_search_fleet" not in result["error"]


@pytest.mark.parametrize(
    ("tool_class", "client_method", "payload"),
    [
        (KubernetesListNodesTool, "list_nodes", {"nodes": [], "total": 0}),
        (KubernetesListConfigMapsTool, "list_configmaps", {"configmaps": [], "total": 0}),
        (KubernetesListServicesTool, "list_services", {"services": [], "total": 0}),
        (KubernetesListIngressesTool, "list_ingresses", {"ingresses": [], "total": 0}),
        (KubernetesGetEventsTool, "get_events", {"events": [], "total": 0}),
        (KubernetesListNamespacesTool, "list_namespaces", {"namespaces": [], "total": 0}),
    ],
    ids=["nodes", "configmaps", "services", "ingresses", "events", "namespaces"],
)
def test_tools_outside_the_workload_existence_set_never_note_scope(
    tool_class: Any, client_method: str, payload: dict[str, Any]
) -> None:
    """These tools never got the Tier A note wiring — pin that boundary.

    A namespace with running pods but no Service object legitimately returns
    zero; firing the fleet-scope caveat there would misdirect the reader.
    """
    with patch(f"integrations.kubernetes.client.KubernetesClient.{client_method}") as mock_call:
        mock_call.return_value = {"success": True, **payload}
        result = tool_class().run(
            kubeconfig=_MINIMAL_KUBECONFIG, cluster_configs=_FLEET_SCOPE_CLUSTER_CONFIGS
        )

    assert "note" not in result
