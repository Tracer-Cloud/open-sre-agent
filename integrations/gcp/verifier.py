"""GCP integration verifier — live Resource Manager probe.

Auto-discovered by ``integrations/_verifiers_loader.py``; no central import
list to update.
"""

from __future__ import annotations

from typing import Any

from integrations.config_models import GCPIntegrationConfig
from integrations.gcp.client import (
    RESOURCE_MANAGER_API,
    GCPClientError,
    build_service,
    describe_api_error,
)
from integrations.gcp.project_discovery import MAX_DISCOVERED, discover
from integrations.verification import register_verifier, result


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _reach(cfg: GCPIntegrationConfig) -> str:
    """Describe how many projects this credential actually reaches.

    ``all_projects`` is what configuration *names*, which is the wrong answer
    under ``GCP_ADDITIONAL_PROJECTS=discover``: there the whole point is that
    the estate is not written down. Reporting "1 project" for a credential that
    reads twelve reads as a misconfiguration, and sends the operator looking for
    a fault that is not there.

    The listing is the memoized one the tools use, not a fresh probe. Verify
    answers "what will a tool see" — a fresh probe could report a reach the next
    tool call does not have, and would cost a round trip the cache already paid.
    """
    configured = cfg.all_projects
    if not cfg.discovery_requested:
        return _plural(len(configured), "project")

    found = discover(cfg)
    if found.error:
        # Still ``passed``: the tools work, on a narrower estate than asked for.
        return (
            f"{_plural(len(configured), 'configured project')}; "
            f"project discovery unavailable ({found.error})"
        )
    reach = _plural(len({*configured, *found.projects}), "discovered project")
    return f"{reach}, capped at {MAX_DISCOVERED}" if found.truncated else reach


@register_verifier("gcp")
def verify_gcp(source: str, config: dict[str, Any]) -> dict[str, str]:
    """Confirm credentials resolve and the primary project is readable.

    A live call rather than a config-presence check, matching the AWS STS
    verifier. It is worth the round-trip here: the failure this catches —
    credentials that resolve to the wrong principal — is otherwise invisible
    until the first investigation, and surfaces as a bare 403.
    """
    try:
        cfg = GCPIntegrationConfig.model_validate(config)
    except Exception as exc:
        return result("gcp", source, "missing", str(exc))

    if not cfg.project_id:
        return result("gcp", source, "missing", "Missing project_id.")

    try:
        service = build_service(cfg, RESOURCE_MANAGER_API)
        project = service.projects().get(projectId=cfg.project_id).execute()
    except GCPClientError as exc:
        return result("gcp", source, "failed", str(exc))
    except Exception as exc:
        return result(
            "gcp",
            source,
            "failed",
            f"Could not read project {cfg.project_id}: {describe_api_error(exc)}",
        )

    name = str(project.get("name", "")).strip() or cfg.project_id
    reach = _reach(cfg)
    mode = (
        "service-account key"
        if cfg.service_account_key
        else ("impersonation" if cfg.impersonate_service_account else "ADC")
    )
    return result(
        "gcp",
        source,
        "passed",
        f"Connected to GCP project {name} ({cfg.project_id}) via {mode}; scope covers {reach}.",
    )
