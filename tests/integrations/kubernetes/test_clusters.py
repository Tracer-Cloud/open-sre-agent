"""Tests for kubernetes cluster management (integrations/kubernetes/clusters.py)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from integrations import store
from integrations.kubernetes import clusters


def _passed(_source: str, _config: dict) -> dict[str, str]:
    return {"service": "kubernetes", "source": "add-cluster", "status": "passed", "detail": "ok"}


def _failed(_source: str, _config: dict) -> dict[str, str]:
    return {
        "service": "kubernetes",
        "source": "add-cluster",
        "status": "failed",
        "detail": "403 Forbidden",
    }


def test_add_cluster_requires_a_kubeconfig_source(tmp_path: Path) -> None:
    store_file = tmp_path / "integrations.json"
    with patch("integrations.store.STORE_PATH", store_file):
        result = clusters.add_cluster(name="gke-prod")
    assert result.ok is False
    assert "kubeconfig" in result.detail


def test_add_cluster_rejects_both_sources(tmp_path: Path) -> None:
    store_file = tmp_path / "integrations.json"
    with patch("integrations.store.STORE_PATH", store_file):
        result = clusters.add_cluster(
            name="gke-prod", kubeconfig_path="~/.kube/config", kubeconfig="apiVersion: v1"
        )
    assert result.ok is False
    assert "only one" in result.detail.lower()


def test_add_cluster_verifies_before_storing(tmp_path: Path) -> None:
    store_file = tmp_path / "integrations.json"
    with (
        patch("integrations.store.STORE_PATH", store_file),
        patch.object(clusters, "verify_kubernetes", _failed),
    ):
        result = clusters.add_cluster(name="gke-prod", kubeconfig_path="/bad")
    assert result.ok is False
    assert "403" in result.detail
    # Nothing persisted on a failed probe.
    with patch("integrations.store.STORE_PATH", store_file):
        assert store.get_instances("kubernetes") == []


def test_add_cluster_persists_named_instance(tmp_path: Path) -> None:
    store_file = tmp_path / "integrations.json"
    with (
        patch("integrations.store.STORE_PATH", store_file),
        patch.object(clusters, "verify_kubernetes", _passed),
    ):
        result = clusters.add_cluster(
            name="GKE-Prod",
            kubeconfig_path="~/.kube/config",
            context="ctx-prod",
            namespace="prod",
            tags={"env": "prod"},
        )
        assert result.ok is True
        instances = store.get_instances("kubernetes")

    assert len(instances) == 1
    inst = instances[0]
    assert inst["name"] == "gke-prod"  # normalized lowercase
    assert inst["tags"] == {"env": "prod"}
    assert inst["credentials"]["kubeconfig_path"] == "~/.kube/config"
    assert inst["credentials"]["context"] == "ctx-prod"
    assert inst["credentials"]["namespace"] == "prod"
    # Empty inline kubeconfig is dropped, not stored blank.
    assert "kubeconfig" not in inst["credentials"]


def test_add_cluster_can_skip_verification(tmp_path: Path) -> None:
    store_file = tmp_path / "integrations.json"
    with patch("integrations.store.STORE_PATH", store_file):
        result = clusters.add_cluster(name="gke-dev", kubeconfig_path="/k", verify=False)
        assert result.ok is True
        assert [c.name for c in clusters.list_clusters()] == ["gke-dev"]


def test_add_cluster_updates_existing_by_name(tmp_path: Path) -> None:
    store_file = tmp_path / "integrations.json"
    with (
        patch("integrations.store.STORE_PATH", store_file),
        patch.object(clusters, "verify_kubernetes", _passed),
    ):
        clusters.add_cluster(name="gke-prod", kubeconfig_path="/old", namespace="prod")
        clusters.add_cluster(name="gke-prod", kubeconfig_path="/new", namespace="prod")
        instances = store.get_instances("kubernetes")

    assert len(instances) == 1
    assert instances[0]["credentials"]["kubeconfig_path"] == "/new"


def test_list_and_remove_clusters(tmp_path: Path) -> None:
    store_file = tmp_path / "integrations.json"
    with (
        patch("integrations.store.STORE_PATH", store_file),
        patch.object(clusters, "verify_kubernetes", _passed),
    ):
        clusters.add_cluster(name="gke-dev", kubeconfig_path="/d", tags={"env": "dev"})
        clusters.add_cluster(name="gke-prod", kubeconfig_path="/p", tags={"env": "prod"})

        listed = clusters.list_clusters()
        assert [c.name for c in listed] == ["gke-dev", "gke-prod"]
        assert listed[0].tags == {"env": "dev"}

        removed = clusters.remove_cluster("gke-dev")
        assert removed.ok is True
        assert [c.name for c in clusters.list_clusters()] == ["gke-prod"]

        # Removing an unknown cluster reports, does not raise.
        missing = clusters.remove_cluster("ghost")
        assert missing.ok is False
        assert "ghost" in missing.detail
