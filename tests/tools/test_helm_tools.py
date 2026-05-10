"""Tests for Helm investigation tools and evidence mapping."""

from __future__ import annotations

from typing import Any

import pytest

from app.nodes.investigate.execution.execute_actions import ActionExecutionResult
from app.nodes.investigate.processing.post_process import build_evidence_summary, merge_evidence
from app.nodes.root_cause_diagnosis.evidence_checker import (
    check_evidence_availability,
    is_clearly_healthy,
)
from app.tools.HelmTools import (
    HelmGetReleaseManifestTool,
    HelmGetReleaseValuesTool,
    HelmListReleasesTool,
    HelmReleaseStatusTool,
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
        "app.tools.HelmTools.helm_client_for_run",
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


def test_helm_evidence_merges_and_counts_for_availability_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.tools.HelmTools.helm_client_for_run",
        lambda *_a, **_k: _FakeHelmClient(),
    )
    list_tool = HelmListReleasesTool()
    status_tool = HelmReleaseStatusTool()

    results = {
        "helm_list_releases": ActionExecutionResult(
            action_name="helm_list_releases",
            success=True,
            data=list_tool.run(**list_tool.extract_params({"helm": _HELM_SOURCE})),
        ),
        "helm_release_status": ActionExecutionResult(
            action_name="helm_release_status",
            success=True,
            data=status_tool.run(**status_tool.extract_params({"helm": _HELM_SOURCE})),
        ),
    }
    evidence = merge_evidence({}, results)
    summary = build_evidence_summary(results)

    assert evidence["helm_releases"]
    assert evidence["helm_release_status"]["info"]["status"] == "deployed"
    _, has_vendor, _ = check_evidence_availability({}, evidence, {})
    assert has_vendor is True
    assert "helm:" in summary


def test_helm_evidence_counts_as_investigated_for_healthy_short_circuit() -> None:
    alert = {"state": "resolved", "commonLabels": {"severity": "info"}, "commonAnnotations": {}}
    assert is_clearly_healthy(alert, {"helm_releases": []}) is True
    assert is_clearly_healthy(alert, {"helm_release_manifest": ""}) is True


def test_helm_get_manifest_tool_returns_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.tools.HelmTools.helm_client_for_run",
        lambda *_a, **_k: _FakeHelmClient(),
    )
    tool = HelmGetReleaseManifestTool()
    result = tool.run(**tool.extract_params({"helm": _HELM_SOURCE}))
    assert result["available"] is True
    assert "kind: Service" in result["manifest"]
