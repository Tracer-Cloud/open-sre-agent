"""Load balancer target health — the bridge from a user-facing symptom to a host."""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.tool_availability import tool_unavailable
from integrations.yandex_cloud.availability import (
    YC_INJECTED_PARAMS,
    client_from_params,
    yc_available_or_backend,
    yc_credentials,
)
from integrations.yandex_cloud.rest_client import YandexCloudClient

SOURCE = "yc_network"

_NLB_SERVICE = "load-balancer"
_NLB_PATH = "/load-balancer/v1/networkLoadBalancers"
_ALB_SERVICE = "alb"
_ALB_PATH = "/apploadbalancer/v1/loadBalancers"

TYPE_NETWORK = "network"
TYPE_APPLICATION = "application"

_HEALTHY_TARGET = "HEALTHY"


def _extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    return yc_credentials(sources)


def _network_balancers(client: YandexCloudClient) -> tuple[list[dict[str, Any]], str]:
    """Return network load balancers with the health of each of their targets."""
    listed = client.get(_NLB_SERVICE, _NLB_PATH, {"folderId": client.folder_id})
    if not listed.get("success"):
        return [], str(listed.get("error", ""))

    balancers: list[dict[str, Any]] = []
    for balancer in (listed.get("data") or {}).get("loadBalancers") or []:
        balancer_id = balancer.get("id", "")
        states = client.get(
            _NLB_SERVICE,
            f"{_NLB_PATH}/{balancer_id}:targetStates",
            {"targetGroupId": _first_target_group(balancer)},
        )
        targets = (states.get("data") or {}).get("targetStates") or []
        balancers.append(
            {
                "id": balancer_id,
                "name": balancer.get("name", ""),
                "type": TYPE_NETWORK,
                "status": balancer.get("status", ""),
                "listeners": len(balancer.get("listeners") or []),
                "targets": [
                    {
                        "address": target.get("address", ""),
                        "subnet_id": target.get("subnetId", ""),
                        "status": target.get("status", ""),
                        "healthy": target.get("status") == _HEALTHY_TARGET,
                    }
                    for target in targets
                ],
                "target_states_error": ""
                if states.get("success")
                else str(states.get("error", "")),
            }
        )
    return balancers, ""


def _first_target_group(balancer: dict[str, Any]) -> str:
    attachments = balancer.get("attachedTargetGroups") or []
    return str(attachments[0].get("targetGroupId", "")) if attachments else ""


def _application_balancers(client: YandexCloudClient) -> tuple[list[dict[str, Any]], str]:
    """Return application load balancers with their backend-group health."""
    listed = client.get(_ALB_SERVICE, _ALB_PATH, {"folderId": client.folder_id})
    if not listed.get("success"):
        return [], str(listed.get("error", ""))

    balancers: list[dict[str, Any]] = []
    for balancer in (listed.get("data") or {}).get("loadBalancers") or []:
        balancer_id = balancer.get("id", "")
        states = client.get(_ALB_SERVICE, f"{_ALB_PATH}/{balancer_id}:getTargetStates")
        balancers.append(
            {
                "id": balancer_id,
                "name": balancer.get("name", ""),
                "type": TYPE_APPLICATION,
                "status": balancer.get("status", ""),
                "listeners": len(balancer.get("listeners") or []),
                "target_states": (states.get("data") or {}) if states.get("success") else {},
                "target_states_error": ""
                if states.get("success")
                else str(states.get("error", "")),
            }
        )
    return balancers, ""


@tool(
    name="get_yc_lb_health",
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.ACTION),
    display_name="Load Balancers",
    source=SOURCE,
    description=(
        "Report load balancer health and the state of the targets behind it. "
        "This is what turns a user-facing symptom — errors at the edge, partial "
        "failures, intermittent timeouts — into a specific unhealthy host. A "
        "balancer with some targets unhealthy explains intermittent errors far "
        "better than any single instance's metrics do."
    ),
    use_cases=[
        "Explaining intermittent 5xx errors by finding partially unhealthy targets",
        "Mapping a load balancer to the instances actually serving traffic",
        "Confirming whether a health check is failing for one host or all of them",
        "Checking that a balancer has any healthy targets left at all",
    ],
    requires=[],
    outputs={
        "balancers": "each balancer with its status and target health",
        "unhealthy_targets": "targets failing their health check, across all balancers",
        "count": "how many balancers were returned",
    },
    input_schema={
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "description": "Restrict to one balancer type. Omit for both.",
                "enum": ["network", "application", ""],
                "default": "",
            }
        },
        "required": [],
    },
    is_available=yc_available_or_backend,
    extract_params=_extract_params,
    injected_params=YC_INJECTED_PARAMS,
)
def get_yc_lb_health(
    type: str = "",  # noqa: A002 - schema-facing name, matches how the model reads it
    yc_backend: Any = None,
    **credentials: Any,
) -> dict[str, Any]:
    """Report load balancer and target health."""
    if yc_backend is not None:
        return dict(yc_backend.get_yc_lb_health(type))

    client = client_from_params(credentials)
    if client is None:
        return tool_unavailable(SOURCE, "Yandex Cloud credentials are not configured.")

    wanted = type.strip().lower()
    balancers: list[dict[str, Any]] = []
    errors: list[str] = []

    if wanted in ("", TYPE_NETWORK):
        found, error = _network_balancers(client)
        balancers.extend(found)
        if error:
            errors.append(f"network: {error}")

    if wanted in ("", TYPE_APPLICATION):
        found, error = _application_balancers(client)
        balancers.extend(found)
        if error:
            errors.append(f"application: {error}")

    unhealthy = [
        {"balancer": balancer["name"], **target}
        for balancer in balancers
        for target in balancer.get("targets", [])
        if not target.get("healthy", True)
    ]

    result: dict[str, Any] = {
        "source": SOURCE,
        "available": True,
        "balancers": balancers,
        "unhealthy_targets": unhealthy,
        "count": len(balancers),
    }
    if errors and not balancers:
        result["available"] = False
        result["error"] = "; ".join(errors)
    elif errors:
        result["note"] = "Partial results: " + "; ".join(errors)
    return result


__all__ = ["get_yc_lb_health"]
