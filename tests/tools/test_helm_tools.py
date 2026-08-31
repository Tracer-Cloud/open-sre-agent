"""Tests for Helm investigation tools and evidence mapping."""

from __future__ import annotations

from typing import Any

import pytest

from integrations.helm.tools import (
    HelmGetReleaseManifestTool,
    HelmGetReleaseValuesTool,
    HelmListReleasesTool,
    HelmReleaseStatusTool,
)
from integrations.helm.tools._evidence import (
    map_helm_get_release_manifest,
    map_helm_get_release_values,
    map_helm_list_releases,
    map_helm_release_history,
    map_helm_release_status,
)


class _FakeHelmClient:
    @property
    def is_configured(self) -> bool:
        return True

    def list_releases(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "success": True,
            "error": "",
            "releases": [{"name": "demo", "namespace": "demo"}],
            **kwargs,
        }

    def release_status(self, release: str, namespace: str) -> dict[str, Any]:
        return {
            "success": True,
            "error": "",
            "status": {
                "name": release,
                "namespace": namespace,
                "info": {"status": "deployed"},
            },
        }

    def release_history(self, _release: str, _namespace: str, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {
            "success": True,
            "error": "",
            "history": [{"revision": 1, "status": "deployed"}],
        }

    def get_values(self, _release: str, _namespace: str, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {"success": True, "error": "", "values": {"image": {"tag": "1.0"}}}

    def get_manifest(self, _release: str, _namespace: str) -> dict[str, Any]:
        return {
            "success": True,
            "error": "",
            "manifest": "apiVersion: v1\nkind: Service",
            "truncated": False,
        }


_HELM_SOURCE = {
    "helm_path": "helm",
    "kube_context": "",
    "kubeconfig": "",
    "default_namespace": "demo",
    "release_name": "demo",
    "namespace": "demo",
    "integration_id": "h1",
    "connection_verified": True,
}


def test_helm_list_tool_is_available_and_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    # Patch where the name is used — HelmTools binds the import at load time.
    monkeypatch.setattr(
        "integrations.helm.tools.helm_client_for_run",
        lambda *_a, **_k: _FakeHelmClient(),
    )
    tool = HelmListReleasesTool()
    assert tool.is_available({"helm": _HELM_SOURCE}) is True
    params = tool.extract_params({"helm": {**_HELM_SOURCE, "release_name": ""}})
    result = tool.run(**params)
    assert result["available"] is True
    assert result["releases"][0]["name"] == "demo"


def test_helm_release_tools_require_release_name() -> None:
    src = {**_HELM_SOURCE, "release_name": ""}
    assert HelmReleaseStatusTool().is_available({"helm": src}) is False
    assert HelmGetReleaseValuesTool().is_available({"helm": src}) is False


def test_helm_get_manifest_tool_returns_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.helm.tools.helm_client_for_run",
        lambda *_a, **_k: _FakeHelmClient(),
    )
    tool = HelmGetReleaseManifestTool()
    result = tool.run(**tool.extract_params({"helm": _HELM_SOURCE}))
    assert result["available"] is True
    assert "kind: Service" in result["manifest"]


# ---------------------------------------------------------------------------
# Evidence mappers
# ---------------------------------------------------------------------------


def test_map_helm_list_releases_records_entry() -> None:
    evidence: dict[str, Any] = {}
    map_helm_list_releases(
        evidence,
        {
            "available": True,
            "releases": [{"name": "demo"}, {"name": "other"}],
            "all_namespaces": True,
        },
        {"max_releases": 256},
    )
    entries = evidence["catalog_entries"]
    assert len(entries) == 1
    assert entries[0]["source"] == "helm_list_releases"
    assert entries[0]["summary"] == "2 release(s) in all namespaces"


def test_map_helm_list_releases_qualifies_when_capped() -> None:
    evidence: dict[str, Any] = {}
    map_helm_list_releases(
        evidence,
        {"available": True, "releases": [{"name": f"r{i}"} for i in range(5)], "namespace": "ns"},
        {"max_releases": 5},
    )
    assert evidence["catalog_entries"][0]["summary"].startswith("5+ release(s)")


def test_map_helm_list_releases_skips_empty_and_unavailable() -> None:
    evidence: dict[str, Any] = {}
    map_helm_list_releases(evidence, {"available": True, "releases": []}, {})
    assert "catalog_entries" not in evidence

    evidence2: dict[str, Any] = {}
    map_helm_list_releases(evidence2, {"available": False, "error": "binary not found"}, {})
    assert "catalog_entries" not in evidence2


def test_map_helm_release_status_records_entry() -> None:
    evidence: dict[str, Any] = {}
    map_helm_release_status(
        evidence,
        {
            "available": True,
            "release": "demo",
            "namespace": "prod",
            "status": {"info": {"status": "failed"}},
        },
        {},
    )
    entries = evidence["catalog_entries"]
    assert len(entries) == 1
    assert entries[0]["source"] == "helm_release_status"
    assert entries[0]["summary"] == "status: failed for 'demo' (ns: prod)"


def test_map_helm_release_status_skips_empty_status() -> None:
    evidence: dict[str, Any] = {}
    map_helm_release_status(evidence, {"available": True, "status": {}}, {})
    assert "catalog_entries" not in evidence


def test_map_helm_release_history_records_entry() -> None:
    evidence: dict[str, Any] = {}
    map_helm_release_history(
        evidence,
        {
            "available": True,
            "release": "demo",
            "history": [
                {"revision": 1, "status": "superseded"},
                {"revision": 2, "status": "failed"},
            ],
        },
        {},
    )
    entries = evidence["catalog_entries"]
    assert len(entries) == 1
    assert entries[0]["source"] == "helm_release_history"
    assert entries[0]["summary"] == "2 revision(s), 1 failed for 'demo'"


def test_map_helm_release_history_skips_empty() -> None:
    evidence: dict[str, Any] = {}
    map_helm_release_history(evidence, {"available": True, "history": []}, {})
    assert "catalog_entries" not in evidence


def test_map_helm_get_release_values_records_key_count_not_values() -> None:
    """Regression: values may include secrets -- the summary must cite a
    count, never the actual keys or values."""
    evidence: dict[str, Any] = {}
    map_helm_get_release_values(
        evidence,
        {
            "available": True,
            "release": "demo",
            "values": {"apiKey": "super-secret", "replicas": 3},
        },
        {},
    )
    entries = evidence["catalog_entries"]
    assert len(entries) == 1
    assert entries[0]["source"] == "helm_get_release_values"
    assert entries[0]["summary"] == "2 top-level value key(s) retrieved for 'demo'"
    assert "super-secret" not in entries[0]["summary"]
    assert "apiKey" not in entries[0]["summary"]


def test_map_helm_get_release_values_notes_all_values() -> None:
    evidence: dict[str, Any] = {}
    map_helm_get_release_values(
        evidence, {"available": True, "values": {"a": 1}, "all_values": True}, {}
    )
    assert "including chart defaults" in evidence["catalog_entries"][0]["summary"]


def test_map_helm_get_release_values_skips_empty() -> None:
    evidence: dict[str, Any] = {}
    map_helm_get_release_values(evidence, {"available": True, "values": {}}, {})
    assert "catalog_entries" not in evidence


def test_map_helm_get_release_manifest_records_entry() -> None:
    evidence: dict[str, Any] = {}
    map_helm_get_release_manifest(
        evidence,
        {
            "available": True,
            "release": "demo",
            "manifest": "apiVersion: v1\nkind: Service",
            "truncated": False,
        },
        {},
    )
    entries = evidence["catalog_entries"]
    assert len(entries) == 1
    assert entries[0]["source"] == "helm_get_release_manifest"
    assert entries[0]["summary"] == "28 char(s) of rendered manifest for 'demo'"


def test_map_helm_get_release_manifest_qualifies_when_truncated() -> None:
    evidence: dict[str, Any] = {}
    map_helm_get_release_manifest(
        evidence, {"available": True, "manifest": "x" * 10, "truncated": True}, {}
    )
    assert evidence["catalog_entries"][0]["summary"].startswith("10+ char(s)")


def test_map_helm_get_release_manifest_skips_empty_and_unavailable() -> None:
    evidence: dict[str, Any] = {}
    map_helm_get_release_manifest(evidence, {"available": True, "manifest": ""}, {})
    assert "catalog_entries" not in evidence

    evidence2: dict[str, Any] = {}
    map_helm_get_release_manifest(evidence2, {"available": False, "error": "not found"}, {})
    assert "catalog_entries" not in evidence2
