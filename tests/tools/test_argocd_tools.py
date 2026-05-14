"""Tests for Argo CD investigation tools."""

from __future__ import annotations

from typing import Any

from app.tools.ArgoCDApplicationDiffTool import ArgoCDApplicationDiffTool
from app.tools.ArgoCDApplicationStatusTool import ArgoCDApplicationStatusTool


class _FakeArgoCDClient:
    def __enter__(self) -> _FakeArgoCDClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def get_application_summary(self, application_name: str, **_: Any) -> dict[str, Any]:
        return {
            "success": True,
            "application": {
                "name": application_name,
                "sync_status": "OutOfSync",
                "health_status": "Degraded",
                "revision": "abc123",
            },
            "recent_history": [{"revision": "abc123", "deployedAt": "2026-04-02T00:00:00Z"}],
        }

    def list_applications(self, **_: Any) -> dict[str, Any]:
        return {"success": True, "applications": [{"name": "payments-api"}], "total": 1}

    def get_application_diff(self, application_name: str, **_: Any) -> dict[str, Any]:
        return {
            "success": True,
            "application_name": application_name,
            "drift_detected": True,
            "diffs": [{"kind": "Deployment", "name": application_name, "diff": "replicas changed"}],
            "diff_count": 1,
        }


_ARGOCD_SOURCE = {
    "base_url": "https://argocd.example.com",
    "bearer_token": "tok_test",
    "username": "",
    "password": "",
    "project": "default",
    "app_namespace": "argocd",
    "application_name": "payments-api",
    "verify_ssl": True,
    "connection_verified": True,
}


def test_argocd_status_tool_extracts_params_and_returns_application(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "app.tools.ArgoCDApplicationStatusTool.make_argocd_client",
        lambda *_args, **_kwargs: _FakeArgoCDClient(),
    )
    tool = ArgoCDApplicationStatusTool()

    assert tool.is_available({"argocd": _ARGOCD_SOURCE}) is True
    params = tool.extract_params({"argocd": _ARGOCD_SOURCE})
    result = tool.run(**params)

    assert result["available"] is True
    assert result["application"]["name"] == "payments-api"
    assert result["application"]["sync_status"] == "OutOfSync"
    assert result["recent_history"][0]["revision"] == "abc123"


def test_argocd_diff_tool_reports_drift(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "app.tools.ArgoCDApplicationDiffTool.make_argocd_client",
        lambda *_args, **_kwargs: _FakeArgoCDClient(),
    )
    tool = ArgoCDApplicationDiffTool()

    result = tool.run(**tool.extract_params({"argocd": _ARGOCD_SOURCE}))

    assert result["available"] is True
    assert result["drift_detected"] is True
    assert result["diffs"][0]["kind"] == "Deployment"


def test_argocd_tools_require_configured_source() -> None:
    assert ArgoCDApplicationStatusTool().is_available({}) is False
    assert (
        ArgoCDApplicationDiffTool().is_available({"argocd": {"connection_verified": False}})
        is False
    )
