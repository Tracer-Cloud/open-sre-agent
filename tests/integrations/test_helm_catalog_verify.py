"""Catalog, env loading, and verification coverage for Helm."""

from __future__ import annotations

import pytest

from app.integrations.catalog import classify_integrations, resolve_effective_integrations
from app.integrations.models import HelmIntegrationConfig
from app.integrations.verify import _verify_helm, verify_integrations


@pytest.fixture(autouse=True)
def _clear_helm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "OSRE_HELM_INTEGRATION",
        "HELM_PATH",
        "HELM_KUBE_CONTEXT",
        "HELM_KUBECONFIG",
        "HELM_NAMESPACE",
    ):
        monkeypatch.delenv(name, raising=False)


def test_classify_helm_store_record() -> None:
    resolved = classify_integrations(
        [
            {
                "id": "helm-store-1",
                "service": "helm",
                "status": "active",
                "credentials": {
                    "helm_path": "helm",
                    "kube_context": "kind-demo",
                    "kubeconfig": "~/.kube/config",
                    "default_namespace": "tracer",
                },
            }
        ]
    )

    cfg = resolved["helm"]
    assert cfg["helm_path"] == "helm"
    assert cfg["kube_context"] == "kind-demo"
    assert cfg["kubeconfig"] == "~/.kube/config"
    assert cfg["default_namespace"] == "tracer"
    assert cfg["integration_id"] == "helm-store-1"


def test_classify_helm_accepts_alternate_credential_keys() -> None:
    resolved = classify_integrations(
        [
            {
                "id": "helm-alt",
                "service": "helm",
                "status": "active",
                "credentials": {
                    "context": "ctx-1",
                    "kubeconfig_path": "/tmp/kc",
                    "namespace": "prod",
                },
            }
        ]
    )
    assert resolved["helm"]["kube_context"] == "ctx-1"
    assert resolved["helm"]["kubeconfig"] == "/tmp/kc"
    assert resolved["helm"]["default_namespace"] == "prod"


def test_resolve_effective_integrations_includes_helm_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.integrations.catalog.load_integrations", lambda: [])
    monkeypatch.setenv("OSRE_HELM_INTEGRATION", "1")
    monkeypatch.setenv("HELM_PATH", "helm")
    monkeypatch.setenv("HELM_KUBE_CONTEXT", "kind-test")
    monkeypatch.setenv("HELM_NAMESPACE", "default")

    effective = resolve_effective_integrations()
    helm = effective.get("helm")
    assert helm is not None
    assert helm["source"] == "local env"
    assert helm["config"]["kube_context"] == "kind-test"
    assert helm["config"]["default_namespace"] == "default"


def test_helm_integration_config_validator_strips_whitespace() -> None:
    cfg = HelmIntegrationConfig(
        helm_path=" helm ",
        kube_context=" ctx ",
        kubeconfig=" ",
        integration_id=" x ",
    )
    assert cfg.helm_path == "helm"
    assert cfg.kube_context == "ctx"
    assert cfg.kubeconfig == ""
    assert cfg.integration_id == "x"


def test_verify_helm_passes_with_working_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations.probes import ProbeResult
    from app.services.helm.client import HelmClient

    monkeypatch.setattr(
        HelmClient,
        "probe_access",
        lambda _self: ProbeResult.passed(
            "Helm CLI is available and can reach the Kubernetes cluster."
        ),
    )

    result = _verify_helm(
        "local env",
        {"helm_path": "helm", "kube_context": "", "kubeconfig": ""},
    )
    assert result["status"] == "passed"


def test_verify_integrations_dispatches_to_helm(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations.probes import ProbeResult
    from app.services.helm.client import HelmClient

    monkeypatch.setattr(
        HelmClient,
        "probe_access",
        lambda _self: ProbeResult.passed(
            "Helm CLI is available and can reach the Kubernetes cluster."
        ),
    )
    monkeypatch.setattr(
        "app.integrations.catalog.load_integrations",
        lambda: [
            {
                "id": "helm-1",
                "service": "helm",
                "status": "active",
                "credentials": {"helm_path": "helm"},
            }
        ],
    )

    results = verify_integrations("helm")
    assert len(results) == 1
    assert results[0]["service"] == "helm"
    assert results[0]["status"] == "passed"
