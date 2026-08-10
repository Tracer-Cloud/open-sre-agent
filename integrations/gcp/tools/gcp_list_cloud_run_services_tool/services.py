"""Cloud Run ``Service`` normalization.

A ``run/v2`` service resource is mostly deployment machinery. The questions an
incident actually asks of it are narrower: is the service serving, which
revision is taking the traffic, what image is that revision running, and did the
last deploy fail. Those are the fields kept here.

The traffic split matters more than it looks. Cloud Run keeps serving the
previous revision when a new one never becomes ready, so a broken deploy shows
up as ``latest_created_revision != latest_ready_revision`` while the service
still reports ``Ready`` — the symptom being "my fix did not take effect", not an
outage.

Kept separate from the tool entrypoint so shape handling is testable without an
API client.
"""

from __future__ import annotations

from typing import Any

#: ``terminalCondition.type`` when the service as a whole is serving.
READY = "Ready"

#: ``state`` of a condition that is satisfied.
CONDITION_TRUE = "CONDITION_SUCCEEDED"


def service_location(name: str) -> str:
    """Extract the region from a ``projects/p/locations/l/services/s`` name."""
    parts = name.split("/")
    return parts[3] if len(parts) > 3 else ""


def short_name(name: str) -> str:
    """Return the trailing id of a fully qualified resource name."""
    return name.rsplit("/", 1)[-1] if name else ""


def _sub_object(parent: dict[str, Any], key: str) -> dict[str, Any]:
    """Return ``parent[key]`` when it is an object, otherwise an empty one."""
    value = parent.get(key)
    return value if isinstance(value, dict) else {}


def _images(template: dict[str, Any]) -> list[str]:
    """Return the container images the revision template deploys."""
    containers = template.get("containers")
    if not isinstance(containers, list):
        return []
    return [
        str(container.get("image", ""))
        for container in containers
        if isinstance(container, dict) and container.get("image")
    ]


def _traffic(service: dict[str, Any]) -> list[dict[str, Any]]:
    """Render the *actual* traffic split, not the requested one.

    ``traffic`` is intent and ``trafficStatuses`` is what is live; they diverge
    for exactly as long as a rollout is stuck, which is the window an
    investigation runs in.
    """
    statuses = service.get("trafficStatuses")
    if not isinstance(statuses, list):
        return []
    split: list[dict[str, Any]] = []
    for status in statuses:
        if not isinstance(status, dict):
            continue
        entry: dict[str, Any] = {
            "revision": short_name(str(status.get("revision", ""))),
            "percent": status.get("percent", 0),
        }
        tag = str(status.get("tag", "")).strip()
        if tag:
            entry["tag"] = tag
        split.append(entry)
    return split


def _failing_conditions(service: dict[str, Any]) -> list[str]:
    """Render every unsatisfied condition as one line.

    Cloud Run puts the real reason a deploy failed here — an image that cannot
    be pulled, a container that exits before binding the port, a missing secret
    — while ``terminalCondition.type`` says only that Ready is false.
    """
    lines: list[str] = []
    conditions = service.get("conditions")
    candidates = conditions if isinstance(conditions, list) else []
    terminal = _sub_object(service, "terminalCondition")
    for condition in [terminal, *candidates]:
        if not isinstance(condition, dict) or not condition:
            continue
        if condition.get("state") == CONDITION_TRUE:
            continue
        kind = str(condition.get("type", "")).strip()
        reason = str(
            condition.get("reason")
            or condition.get("revisionReason")
            or condition.get("executionReason")
            or ""
        ).strip()
        message = str(condition.get("message", "")).strip()
        detail = ": ".join(part for part in (reason, message) if part)
        rendered = f"{kind} — {detail}" if kind and detail else detail or kind
        if rendered and rendered not in lines:
            lines.append(rendered)
    return lines


def normalize_service(service: dict[str, Any], project: str) -> dict[str, Any]:
    """Flatten one Cloud Run service into the compact shape the agent consumes."""
    name = str(service.get("name", ""))
    terminal = _sub_object(service, "terminalCondition")
    ready = terminal.get("type") == READY and terminal.get("state") == CONDITION_TRUE
    created = short_name(str(service.get("latestCreatedRevision", "")))
    serving = short_name(str(service.get("latestReadyRevision", "")))
    failures = _failing_conditions(service)
    scaling = _sub_object(service, "scaling")
    template = _sub_object(service, "template")
    template_scaling = _sub_object(template, "scaling")
    labels = service.get("labels")

    normalized: dict[str, Any] = {
        "project": project,
        "name": short_name(name),
        "location": service_location(name),
        "ready": bool(ready),
        # A rollout in flight and a rollout that never landed look identical in
        # a single snapshot, so both are reported rather than judged.
        "latest_created_revision": created,
        "latest_ready_revision": serving,
        "rollout_pending": bool(created and serving and created != serving),
        "ingress": str(service.get("ingress", "")),
        "last_deployed_at": str(service.get("updateTime", "")),
        "last_modified_by": str(service.get("lastModifier", "")),
    }

    uri = str(service.get("uri", "")).strip()
    if uri:
        normalized["url"] = uri
    images = _images(template)
    if images:
        normalized["images"] = images
    traffic = _traffic(service)
    if traffic:
        normalized["traffic"] = traffic
    minimum = scaling.get("minInstanceCount", template_scaling.get("minInstanceCount"))
    maximum = template_scaling.get("maxInstanceCount")
    if minimum is not None:
        normalized["min_instances"] = minimum
    if maximum is not None:
        normalized["max_instances"] = maximum
    if service.get("reconciling"):
        normalized["reconciling"] = True
    if failures:
        normalized["failing_conditions"] = failures
    if isinstance(labels, dict) and labels:
        normalized["labels"] = {str(key): str(value) for key, value in labels.items()}
    return normalized


def normalize_services(services: list[Any], project: str) -> list[dict[str, Any]]:
    """Normalize a listing, skipping anything that is not an object."""
    return [normalize_service(item, project) for item in services if isinstance(item, dict)]
