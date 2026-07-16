"""Tests for the Kubernetes integration: config, classify, and client probe."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from integrations.config_models import KubernetesIntegrationConfig
from integrations.kubernetes import classify

_MINIMAL_KUBECONFIG = (
    "apiVersion: v1\n"
    "clusters: []\n"
    "contexts: []\n"
    "current-context: ''\n"
    "kind: Config\n"
    "preferences: {}\n"
    "users: []\n"
)


# ---------------------------------------------------------------------------
# Config model
# ---------------------------------------------------------------------------


def test_kubernetes_config_validates_minimal() -> None:
    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    assert cfg.kubeconfig == _MINIMAL_KUBECONFIG.strip()
    assert cfg.context == ""
    assert cfg.namespace == "default"
    assert cfg.is_configured is True


def test_kubernetes_config_strips_context_whitespace() -> None:
    cfg = KubernetesIntegrationConfig.model_validate(
        {"kubeconfig": _MINIMAL_KUBECONFIG, "context": "  my-ctx  "}
    )
    assert cfg.context == "my-ctx"


def test_kubernetes_config_defaults_namespace_when_empty() -> None:
    cfg = KubernetesIntegrationConfig.model_validate(
        {"kubeconfig": _MINIMAL_KUBECONFIG, "namespace": ""}
    )
    assert cfg.namespace == "default"


def test_kubernetes_config_is_configured_false_when_both_empty() -> None:
    cfg = KubernetesIntegrationConfig(kubeconfig="", kubeconfig_path="")
    assert cfg.is_configured is False


def test_kubernetes_config_is_configured_true_with_path_only() -> None:
    cfg = KubernetesIntegrationConfig(kubeconfig_path="/home/user/.kube/config")
    assert cfg.is_configured is True


def test_kubernetes_config_accepts_missing_kubeconfig() -> None:
    # Both kubeconfig and kubeconfig_path are optional — empty config is valid
    cfg = KubernetesIntegrationConfig.model_validate({})
    assert cfg.is_configured is False


# ---------------------------------------------------------------------------
# classify()
# ---------------------------------------------------------------------------


def test_classify_returns_config_and_key() -> None:
    cfg, key = classify({"kubeconfig": _MINIMAL_KUBECONFIG}, "rec-1")
    assert key == "kubernetes"
    assert cfg is not None
    assert cfg.kubeconfig == _MINIMAL_KUBECONFIG.strip()


def test_classify_returns_none_when_kubeconfig_missing() -> None:
    cfg, key = classify({}, "rec-2")
    assert cfg is None
    assert key is None


def test_classify_preserves_context_and_namespace() -> None:
    cfg, key = classify(
        {"kubeconfig": _MINIMAL_KUBECONFIG, "context": "staging", "namespace": "prod"},
        "rec-3",
    )
    assert key == "kubernetes"
    assert cfg is not None
    assert cfg.context == "staging"
    assert cfg.namespace == "prod"


# ---------------------------------------------------------------------------
# KubernetesClient._build_clients()
# ---------------------------------------------------------------------------


def test_build_clients_passes_single_kubeconfig_path_through() -> None:
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig(kubeconfig_path="/home/user/.kube/config")
    client = KubernetesClient(cfg)

    with patch("integrations.kubernetes.client.k8s_config.load_kube_config") as mock_load:
        client._build_clients()

    assert mock_load.call_args.kwargs["config_file"] == "/home/user/.kube/config"


def test_build_clients_passes_colon_separated_kubeconfig_path_through() -> None:
    """Colon-separated paths must reach the SDK's own KubeConfigMerger, not fall
    back to reading the process environment's KUBECONFIG variable."""
    from integrations.kubernetes.client import KubernetesClient

    colon_path = "/home/user/.kube/config:/home/user/.kube/dev"
    cfg = KubernetesIntegrationConfig(kubeconfig_path=colon_path)
    client = KubernetesClient(cfg)

    with patch("integrations.kubernetes.client.k8s_config.load_kube_config") as mock_load:
        client._build_clients()

    assert mock_load.call_args.kwargs["config_file"] == colon_path


# ---------------------------------------------------------------------------
# KubernetesClient.probe_access()
# ---------------------------------------------------------------------------


def test_probe_access_missing_when_not_configured() -> None:
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig(kubeconfig="", kubeconfig_path="")
    client = KubernetesClient(cfg)
    result = client.probe_access()
    assert result.status == "missing"
    assert "kubeconfig" in result.detail.lower()


def test_probe_access_passed_when_api_succeeds() -> None:
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    client = KubernetesClient(cfg)

    mock_pod = MagicMock()
    mock_pod_list = MagicMock()
    mock_pod_list.items = [mock_pod]

    mock_core = MagicMock()
    mock_core.list_namespaced_pod.return_value = mock_pod_list

    with patch.object(client, "_get_clients", return_value=(mock_core, MagicMock(), MagicMock())):
        result = client.probe_access()

    assert result.ok
    assert "default" in result.detail


def test_probe_access_failed_on_api_exception() -> None:
    from kubernetes.client.exceptions import ApiException

    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    client = KubernetesClient(cfg)

    mock_core = MagicMock()
    mock_core.list_namespaced_pod.side_effect = ApiException(status=401, reason="Unauthorized")

    with patch.object(client, "_get_clients", return_value=(mock_core, MagicMock(), MagicMock())):
        result = client.probe_access()

    assert result.status == "failed"
    assert "401" in result.detail


# ---------------------------------------------------------------------------
# Env loader (catalog integration)
# ---------------------------------------------------------------------------


def test_env_loader_reads_kubeconfig_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KUBECONFIG_CONTENT", _MINIMAL_KUBECONFIG)
    monkeypatch.setenv("KUBECONFIG_CONTEXT", "test-ctx")
    monkeypatch.setenv("KUBECONFIG_NAMESPACE", "kube-system")
    monkeypatch.delenv("KUBECONFIG", raising=False)

    from integrations._catalog_impl import load_env_integrations

    records = load_env_integrations()
    k8s_records = [r for r in records if r.get("service") == "kubernetes"]
    assert len(k8s_records) == 1
    creds = k8s_records[0].get("credentials", {})
    assert creds.get("kubeconfig") == _MINIMAL_KUBECONFIG.strip()
    assert creds.get("kubeconfig_path") == ""
    assert creds.get("context") == "test-ctx"
    assert creds.get("namespace") == "kube-system"


def test_env_loader_stores_kubeconfig_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempdirFactory
) -> None:
    kube_file = tmp_path / "config"
    kube_file.write_text(_MINIMAL_KUBECONFIG)
    monkeypatch.setenv("KUBECONFIG", str(kube_file))
    monkeypatch.delenv("KUBECONFIG_CONTENT", raising=False)

    from integrations._catalog_impl import load_env_integrations

    records = load_env_integrations()
    k8s_records = [r for r in records if r.get("service") == "kubernetes"]
    assert len(k8s_records) == 1
    creds = k8s_records[0].get("credentials", {})
    # Path is stored; file content is NOT read at classify time
    assert creds.get("kubeconfig_path") == str(kube_file)
    assert creds.get("kubeconfig") == ""


def test_env_loader_skips_when_no_kubeconfig_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KUBECONFIG", raising=False)
    monkeypatch.delenv("KUBECONFIG_CONTENT", raising=False)

    from integrations._catalog_impl import load_env_integrations

    records = load_env_integrations()
    k8s_records = [r for r in records if r.get("service") == "kubernetes"]
    assert len(k8s_records) == 0
