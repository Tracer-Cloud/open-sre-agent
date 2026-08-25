# ======== from tools/argocd_application_diff_tool/ ========

"""Argo CD application diff/drift investigation tool."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.tool import BaseTool
from core.tool_framework.utils import tool_unavailable
from integrations.argocd.client import make_argocd_client


def _map_argocd_application_diff(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    """Cite detected GitOps drift as report evidence.

    Records nothing when no drift was found -- "0 diffs, no drift" is a clean
    result, not a finding worth spending the agent's context budget on.
    ``drift_detected`` is the authoritative signal, not ``diff_count``: the
    client's ``modified`` flag (Argo CD v3.3's {items, modified} response
    shape) can report drift with an empty itemized diff list -- a modified-
    but-unitemized result must still be cited, not silently dropped.
    """
    if not output.get("available") or not output.get("drift_detected"):
        return
    diff_count = output.get("diff_count") or 0
    if diff_count:
        summary = f"{diff_count} object diff(s) — live state has drifted from GitOps"
    else:
        summary = "live state has drifted from GitOps (no itemized diffs returned)"
    record_evidence_entry(
        evidence,
        source="argocd_application_diff",
        label="Argo CD Application Diff",
        summary=summary,
    )


class ArgoCDApplicationDiffTool(BaseTool):
    """Fetch Argo CD server-side diff data for an application."""

    name = "argocd_application_diff"
    evidence_mapper = _map_argocd_application_diff
    source = "argocd"
    description = (
        "Fetch Argo CD server-side diff output and report whether live cluster state "
        "has drifted from the desired GitOps state."
    )
    use_cases = [
        "Detecting GitOps drift during an incident",
        "Checking whether an OutOfSync application has Kubernetes object diffs",
        "Correlating deployment drift with application health degradation",
    ]
    requires = ["base_url", "application_name"]
    injected_params = ["base_url", "password", "username"]
    input_schema = {
        "type": "object",
        "properties": {
            "base_url": {"type": "string", "description": "Argo CD base URL"},
            "bearer_token": {"type": "string", "default": "", "description": "Argo CD API token"},
            "username": {"type": "string", "default": "", "description": "Argo CD username"},
            "password": {"type": "string", "default": "", "description": "Argo CD password"},
            "application_name": {"type": "string", "description": "Application name"},
            "project": {"type": "string", "default": "", "description": "Optional Argo CD project"},
            "app_namespace": {
                "type": "string",
                "default": "",
                "description": "Optional app namespace",
            },
            "verify_ssl": {
                "type": "boolean",
                "default": True,
                "description": "Verify TLS certificates",
            },
        },
        "required": ["base_url", "application_name"],
    }
    outputs = {
        "drift_detected": "True when Argo CD reports one or more object diffs",
        "diffs": "Sanitized server-side diff records",
        "diff_count": "Number of diff records returned",
    }

    def is_available(self, sources: dict) -> bool:
        argocd = sources.get("argocd", {})
        return bool(
            argocd.get("connection_verified")
            and argocd.get("base_url")
            and argocd.get("application_name")
            and (argocd.get("bearer_token") or (argocd.get("username") and argocd.get("password")))
        )

    def extract_params(self, sources: dict) -> dict[str, Any]:
        argocd = sources["argocd"]
        return {
            "base_url": argocd.get("base_url", ""),
            "bearer_token": argocd.get("bearer_token", ""),
            "username": argocd.get("username", ""),
            "password": argocd.get("password", ""),
            "application_name": argocd.get("application_name", ""),
            "project": argocd.get("project", ""),
            "app_namespace": argocd.get("app_namespace", ""),
            "verify_ssl": argocd.get("verify_ssl", True),
        }

    def run(
        self,
        base_url: str,
        application_name: str,
        bearer_token: str = "",
        username: str = "",
        password: str = "",
        project: str = "",
        app_namespace: str = "",
        verify_ssl: bool = True,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = make_argocd_client(
            base_url,
            bearer_token,
            username,
            password,
            project=project,
            app_namespace=app_namespace,
            verify_ssl=verify_ssl,
        )
        if client is None:
            return tool_unavailable(
                "argocd",
                "Argo CD integration is not configured (missing base_url or auth).",
                application_name=application_name,
                drift_detected=False,
                diffs=[],
                diff_count=0,
            )

        with client:
            result = client.get_application_diff(
                application_name,
                project=project,
                app_namespace=app_namespace,
            )

        if not result.get("success"):
            return tool_unavailable(
                "argocd",
                result.get("error", "unknown error"),
                application_name=application_name,
                drift_detected=False,
                diffs=[],
                diff_count=0,
            )

        return {
            "source": "argocd",
            "available": True,
            **result,
        }


argocd_application_diff = ArgoCDApplicationDiffTool()


# ======== from tools/argocd_application_status_tool/ ========

"""Argo CD application status investigation tool."""


from core.tool import BaseTool


def _map_argocd_application_status(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    """Cite the application's sync/health status, or the application list count."""
    if not output.get("available"):
        return
    application = output.get("application")
    if isinstance(application, dict) and application.get("name"):
        sync_status = application.get("sync_status") or "unknown"
        health_status = application.get("health_status") or "unknown"
        record_evidence_entry(
            evidence,
            source="argocd_application_status",
            label="Argo CD Application Status",
            summary=f"{application['name']}: sync={sync_status}, health={health_status}",
        )
        return
    applications = output.get("applications")
    if isinstance(applications, list) and applications:
        record_evidence_entry(
            evidence,
            source="argocd_application_status",
            label="Argo CD Applications",
            summary=f"{len(applications)} application(s) listed",
        )


class ArgoCDApplicationStatusTool(BaseTool):
    """Fetch Argo CD application sync and health status."""

    name = "argocd_application_status"
    evidence_mapper = _map_argocd_application_status
    source = "argocd"
    description = (
        "Fetch Argo CD application sync status, health status, current revision, "
        "and recent deployment history."
    )
    use_cases = [
        "Checking whether a GitOps application is OutOfSync or Degraded",
        "Correlating an incident with a recent Argo CD deployment revision",
        "Listing visible Argo CD applications when an alert omits the application name",
    ]
    requires = ["base_url"]
    injected_params = ["base_url", "password", "username"]
    input_schema = {
        "type": "object",
        "properties": {
            "base_url": {"type": "string", "description": "Argo CD base URL"},
            "bearer_token": {"type": "string", "default": "", "description": "Argo CD API token"},
            "username": {"type": "string", "default": "", "description": "Argo CD username"},
            "password": {"type": "string", "default": "", "description": "Argo CD password"},
            "application_name": {
                "type": "string",
                "default": "",
                "description": "Application name",
            },
            "project": {"type": "string", "default": "", "description": "Optional Argo CD project"},
            "app_namespace": {
                "type": "string",
                "default": "",
                "description": "Optional app namespace",
            },
            "verify_ssl": {
                "type": "boolean",
                "default": True,
                "description": "Verify TLS certificates",
            },
        },
        "required": ["base_url"],
    }
    outputs = {
        "application": "Application status summary when application_name is provided",
        "applications": "Application list when application_name is omitted",
        "recent_history": "Recent Argo CD deployment history entries",
    }

    def is_available(self, sources: dict) -> bool:
        argocd = sources.get("argocd", {})
        return bool(
            argocd.get("connection_verified")
            and argocd.get("base_url")
            and (argocd.get("bearer_token") or (argocd.get("username") and argocd.get("password")))
        )

    def extract_params(self, sources: dict) -> dict[str, Any]:
        argocd = sources["argocd"]
        return {
            "base_url": argocd.get("base_url", ""),
            "bearer_token": argocd.get("bearer_token", ""),
            "username": argocd.get("username", ""),
            "password": argocd.get("password", ""),
            "application_name": argocd.get("application_name", ""),
            "project": argocd.get("project", ""),
            "app_namespace": argocd.get("app_namespace", ""),
            "verify_ssl": argocd.get("verify_ssl", True),
        }

    def run(
        self,
        base_url: str,
        bearer_token: str = "",
        username: str = "",
        password: str = "",
        application_name: str = "",
        project: str = "",
        app_namespace: str = "",
        verify_ssl: bool = True,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = make_argocd_client(
            base_url,
            bearer_token,
            username,
            password,
            project=project,
            app_namespace=app_namespace,
            verify_ssl=verify_ssl,
        )
        if client is None:
            return tool_unavailable(
                "argocd",
                "Argo CD integration is not configured (missing base_url or auth).",
                application={},
                applications=[],
                recent_history=[],
            )

        with client:
            if application_name:
                result = client.get_application_summary(
                    application_name,
                    project=project,
                    app_namespace=app_namespace,
                )
            else:
                result = client.list_applications(projects=[project] if project else None)

        if not result.get("success"):
            return tool_unavailable(
                "argocd",
                result.get("error", "unknown error"),
                application={},
                applications=[],
                recent_history=[],
            )

        return {
            "source": "argocd",
            "available": True,
            **result,
        }


argocd_application_status = ArgoCDApplicationStatusTool()
