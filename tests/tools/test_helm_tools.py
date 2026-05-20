"""Tests for Helm investigation tools and evidence mapping."""

from __future__ import annotations

from typing import Any

import pytest

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


def test_helm_get_manifest_tool_returns_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.tools.HelmTools.helm_client_for_run",
        lambda *_a, **_k: _FakeHelmClient(),
    )
    tool = HelmGetReleaseManifestTool()
    result = tool.run(**tool.extract_params({"helm": _HELM_SOURCE}))
    assert result["available"] is True
    assert "kind: Service" in result["manifest"]
