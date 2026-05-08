"""Tests for Helm source detection in detect_sources."""

from __future__ import annotations

from app.nodes.plan_actions.detect_sources import detect_sources

_HELM_INT = {
    "helm_path": "helm",
    "kube_context": "kind-demo",
    "kubeconfig": "",
    "default_namespace": "demo",
    "integration_id": "helm-1",
}


def test_helm_source_detected_from_release_annotation() -> None:
    alert = {"annotations": {"helm_release": "payments-api", "helm_namespace": "prod"}}
    sources = detect_sources(alert, {}, {"helm": _HELM_INT})
    helm = sources.get("helm")
    assert helm is not None
    assert helm["release_name"] == "payments-api"
    assert helm["namespace"] == "prod"
    assert helm["connection_verified"] is True


def test_helm_source_detected_from_helm_sh_label() -> None:
    alert = {
        "labels": {"meta.helm.sh/release-name": "demo"},
        "annotations": {},
    }
    sources = detect_sources(alert, {}, {"helm": _HELM_INT})
    assert sources.get("helm") is not None
    assert sources["helm"]["release_name"] == "demo"
    assert sources["helm"]["namespace"] == "demo"


def test_helm_source_uses_meta_helm_release_namespace_label() -> None:
    alert = {
        "labels": {
            "meta.helm.sh/release-name": "myrel",
            "meta.helm.sh/release-namespace": "kube-system",
        },
        "annotations": {},
    }
    sources = detect_sources(alert, {}, {"helm": _HELM_INT})
    assert sources.get("helm") is not None
    assert sources["helm"]["release_name"] == "myrel"
    assert sources["helm"]["namespace"] == "kube-system"


def test_helm_source_detected_from_summary_phrase() -> None:
    alert = {"annotations": {"summary": "Recent helm upgrade failed for nginx"}}
    sources = detect_sources(alert, {}, {"helm": _HELM_INT})
    assert "helm" in sources


def test_helm_source_not_created_for_unrelated_deployment_text() -> None:
    alert = {"annotations": {"summary": "EKS pod deployment failed during rollout"}}
    sources = detect_sources(alert, {}, {"helm": _HELM_INT})
    assert "helm" not in sources


def test_helm_source_not_created_without_integration() -> None:
    alert = {"annotations": {"helm_release": "demo"}}
    sources = detect_sources(alert, {}, {})
    assert "helm" not in sources


def test_helm_detection_handles_non_dict_raw_alert() -> None:
    """Non-dict payloads must not raise when Helm is configured."""
    sources = detect_sources(
        "plain-string-alert-payload",
        {},
        {"helm": _HELM_INT},
    )
    assert "helm" not in sources
