"""Vercel evidence section builder for root cause diagnosis prompts."""

from __future__ import annotations

from typing import Any

from app.nodes.root_cause_diagnosis.formatters import (
    _extract_vercel_git_metadata,
    _format_vercel_runtime_log,
)


def build_vercel_section(
    *,
    vercel_deployment: dict[str, Any],
    vercel_failed_deployments: list[dict[str, Any]],
    vercel_error_events: list[dict[str, Any]],
    vercel_runtime_logs: list[dict[str, Any]],
    vercel_url: str,
) -> str:
    """Build a Vercel deployment evidence section."""
    lines = ["\nVercel Deployment Evidence:\n"]

    if vercel_deployment:
        git_meta = _extract_vercel_git_metadata(vercel_deployment.get("meta", {}))
        lines.append(f"- Deployment ID: {vercel_deployment.get('id', 'unknown')}\n")
        lines.append(f"- State: {vercel_deployment.get('state', 'unknown')}\n")
        if vercel_deployment.get("error"):
            lines.append(f"- Deployment Error: {vercel_deployment.get('error')}\n")
        if git_meta["repo"]:
            lines.append(f"- GitHub Repo: {git_meta['repo']}\n")
        if git_meta["sha"]:
            lines.append(f"- Commit SHA: {git_meta['sha']}\n")
        if git_meta["ref"]:
            lines.append(f"- Commit Ref: {git_meta['ref']}\n")

    if vercel_failed_deployments:
        lines.append(f"Failed Deployments ({len(vercel_failed_deployments)}):\n")
        for deployment in vercel_failed_deployments[:5]:
            if not isinstance(deployment, dict):
                continue
            git_meta = _extract_vercel_git_metadata(deployment.get("meta", {}))
            line = f"- {deployment.get('id', 'unknown')} [{deployment.get('state', 'unknown')}]"
            if deployment.get("error"):
                line += f" error={str(deployment.get('error'))[:140]}"
            if git_meta["sha"]:
                line += f" sha={git_meta['sha'][:12]}"
            if git_meta["ref"]:
                line += f" ref={git_meta['ref']}"
            lines.append(f"{line}\n")

    if vercel_error_events:
        lines.append(f"Vercel Error Events ({len(vercel_error_events)}):\n")
        for event in vercel_error_events[:8]:
            if not isinstance(event, dict):
                continue
            lines.append(f"- {str(event.get('text', ''))[:280]}\n")

    if vercel_runtime_logs:
        lines.append(f"Vercel Runtime Logs ({len(vercel_runtime_logs)}):\n")
        for log in vercel_runtime_logs[:8]:
            formatted = _format_vercel_runtime_log(log)
            if formatted:
                lines.append(f"- {formatted}\n")

    if vercel_url:
        lines.append(f"\n[Citation: View full logs at {vercel_url}]\n")

    return "".join(lines)
