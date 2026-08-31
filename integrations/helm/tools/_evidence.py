"""Evidence mappers for the Helm CLI investigation tools."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry

_FAILED_REVISION_STATUS = "failed"


def map_helm_list_releases(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite the release count and scope, qualifying page-capped totals.

    ``helm list --max`` silently caps the CLI's own output with no
    truncation signal echoed back, so a returned count at that ceiling may
    understate the true number of releases -- use the "N+" convention
    against the caller's requested ``max_releases``.
    """
    if not output.get("available"):
        return
    releases = output.get("releases") or []
    if not releases:
        return
    total = len(releases)
    requested_max = tool_input.get("max_releases", 256)
    truncated = total >= max(requested_max, 1)
    total_label = f"{total}+" if truncated else str(total)
    scope = "all namespaces" if output.get("all_namespaces") else (output.get("namespace") or "")
    summary = f"{total_label} release(s)"
    if scope:
        summary += f" in {scope}"
    record_evidence_entry(
        evidence,
        source="helm_list_releases",
        label="Helm Releases",
        summary=summary,
    )


def map_helm_release_status(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the release's deployment status (helm status's ``info.status``)."""
    if not output.get("available"):
        return
    status = output.get("status") or {}
    if not status:
        return
    info = status.get("info") if isinstance(status.get("info"), dict) else {}
    deploy_status = (info or {}).get("status") or "unknown"
    summary = f"status: {deploy_status}"
    release = output.get("release")
    if release:
        summary += f" for '{release}'"
    namespace = output.get("namespace")
    if namespace:
        summary += f" (ns: {namespace})"
    record_evidence_entry(
        evidence,
        source="helm_release_status",
        label="Helm Release Status",
        summary=summary,
    )


def map_helm_release_history(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the revision count and how many are in a failed state."""
    if not output.get("available"):
        return
    history = output.get("history") or []
    if not history:
        return
    failed = sum(1 for h in history if str(h.get("status", "")).lower() == _FAILED_REVISION_STATUS)
    summary = f"{len(history)} revision(s), {failed} failed"
    release = output.get("release")
    if release:
        summary += f" for '{release}'"
    record_evidence_entry(
        evidence,
        source="helm_release_history",
        label="Helm Release History",
        summary=summary,
    )


def map_helm_get_release_values(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite how many value keys were retrieved -- never the values themselves.

    The tool's own docstring warns values "may include secrets"; citing a
    key count (not the keys or values) keeps the evidence entry safe to
    surface in a report without deciding per-key sensitivity.
    """
    if not output.get("available"):
        return
    values = output.get("values") or {}
    if not values:
        return
    summary = f"{len(values)} top-level value key(s) retrieved"
    if output.get("all_values"):
        summary += " (including chart defaults)"
    release = output.get("release")
    if release:
        summary += f" for '{release}'"
    record_evidence_entry(
        evidence,
        source="helm_get_release_values",
        label="Helm Release Values",
        summary=summary,
    )


def map_helm_get_release_manifest(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the rendered manifest's length, using the client's own truncation flag."""
    if not output.get("available"):
        return
    manifest = output.get("manifest") or ""
    if not manifest:
        return
    count_label = f"{len(manifest)}+" if output.get("truncated") else str(len(manifest))
    summary = f"{count_label} char(s) of rendered manifest"
    release = output.get("release")
    if release:
        summary += f" for '{release}'"
    record_evidence_entry(
        evidence,
        source="helm_get_release_manifest",
        label="Helm Release Manifest",
        summary=summary,
    )
