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


def _normalized_targets(states: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a getTargetStates response into per-target health.

    Both balancer kinds answer with the same ``{"targetStates": [...]}`` shape,
    so both feed the same ``unhealthy_targets`` aggregation downstream. Skipping
    this for one kind is how an unhealthy target goes unreported.
    """
    raw = (states.get("data") or {}).get("targetStates") or []
    return [
        {
            "address": target.get("address", ""),
            "subnet_id": target.get("subnetId", ""),
            "status": target.get("status", ""),
            "healthy": target.get("status") == _HEALTHY_TARGET,
        }
        for target in raw
    ]


def _network_balancers(client: YandexCloudClient) -> tuple[list[dict[str, Any]], str, str]:
    """Return network load balancers, an error, and the token for any further page."""
    listed = client.get(_NLB_SERVICE, _NLB_PATH, {"folderId": client.folder_id})
    if not listed.get("success"):
        return [], str(listed.get("error", "")), ""
    more = str((listed.get("metadata") or {}).get("next_page_token", ""))

    balancers: list[dict[str, Any]] = []
    for balancer in (listed.get("data") or {}).get("loadBalancers") or []:
        balancer_id = balancer.get("id", "")
        states = client.get(
            _NLB_SERVICE,
            f"{_NLB_PATH}/{balancer_id}:getTargetStates",
            {"targetGroupId": _first_target_group(balancer)},
        )
        balancers.append(
            {
                "id": balancer_id,
                "name": balancer.get("name", ""),
                "type": TYPE_NETWORK,
                "status": balancer.get("status", ""),
                "listeners": len(balancer.get("listeners") or []),
                "targets": _normalized_targets(states),
                "target_states_error": ""
                if states.get("success")
                else str(states.get("error", "")),
            }
        )
    return balancers, "", more


def _first_target_group(balancer: dict[str, Any]) -> str:
    attachments = balancer.get("attachedTargetGroups") or []
    return str(attachments[0].get("targetGroupId", "")) if attachments else ""


_ALB_HTTP_ROUTER_PATH = "/apploadbalancer/v1/httpRouters"
_ALB_BACKEND_GROUP_PATH = "/apploadbalancer/v1/backendGroups"


def _alb_router_ids(balancer: dict[str, Any]) -> list[str]:
    """Pull the HTTP router id out of every listener handler shape."""
    ids: list[str] = []
    for listener in balancer.get("listeners") or []:
        for key in ("http", "tls"):
            handler = listener.get(key) or {}
            direct = (handler.get("handler") or {}).get("httpRouterId")
            if direct:
                ids.append(direct)
            nested = ((handler.get("defaultHandler") or {}).get("httpHandler") or {}).get(
                "httpRouterId"
            )
            if nested:
                ids.append(nested)
    return sorted(set(ids))


def _alb_backend_group_ids(client: YandexCloudClient, router_ids: list[str]) -> list[str]:
    """Return the backend groups the balancer's routes point at.

    A route is either HTTP or gRPC, and each names its backend group under its
    own key. Reading only the HTTP form leaves a gRPC route's targets unchecked.
    """
    ids: list[str] = []
    for router_id in router_ids:
        resp = client.get(_ALB_SERVICE, f"{_ALB_HTTP_ROUTER_PATH}/{router_id}/virtualHosts")
        for vhost in (resp.get("data") or {}).get("virtualHosts") or []:
            for route in vhost.get("routes") or []:
                for proto in ("http", "grpc"):
                    backend_group = (route.get(proto) or {}).get("route", {}).get("backendGroupId")
                    if backend_group:
                        ids.append(backend_group)
    return sorted(set(ids))


def _alb_target_group_ids(backend_group: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for kind in ("http", "grpc", "stream"):
        for backend in (backend_group.get(kind) or {}).get("backends") or []:
            ids += (backend.get("targetGroups") or {}).get("targetGroupIds") or []
    return ids


def _alb_targets(states: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten an application targetStates response into per-target health.

    An application target's health is reported per zone; it is healthy overall
    if any zone can reach it, and unhealthy only when every zone fails its
    active health check.
    """
    targets: list[dict[str, Any]] = []
    for entry in (states.get("data") or {}).get("targetStates") or []:
        target = entry.get("target") or {}
        zones = (entry.get("status") or {}).get("zoneStatuses") or []
        healthy = any(zone.get("status") == _HEALTHY_TARGET for zone in zones)
        targets.append(
            {
                "address": target.get("ipAddress", ""),
                "subnet_id": target.get("subnetId", ""),
                "status": _HEALTHY_TARGET if healthy else "UNHEALTHY",
                "healthy": healthy,
            }
        )
    return targets


def _alb_target_health(
    client: YandexCloudClient, lb_id: str, balancer: dict[str, Any]
) -> tuple[list[dict[str, Any]], str]:
    """Walk the balancer's backend graph and collect its targets' health.

    The graph is listener -> HTTP router -> route -> backend group -> target
    group, and only then can targetStates be read. Single-resource reads pass
    page_size=None: Yandex answers a target-state or backend-group read that
    carries a stray pageSize with a bare 404.
    """
    targets: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for bg_id in _alb_backend_group_ids(client, _alb_router_ids(balancer)):
        group = client.get(_ALB_SERVICE, f"{_ALB_BACKEND_GROUP_PATH}/{bg_id}", page_size=None)
        if not group.get("success"):
            errors.append(f"backend group {bg_id}: {group.get('error', '')}")
            continue
        for tg_id in _alb_target_group_ids(group.get("data") or {}):
            path = f"{_ALB_PATH}/{lb_id}/targetStates/{bg_id}/{tg_id}"
            states = client.get(_ALB_SERVICE, path, page_size=None)
            if not states.get("success"):
                errors.append(f"targetStates {bg_id}/{tg_id}: {states.get('error', '')}")
                continue
            for target in _alb_targets(states):
                if target["address"] not in seen:
                    seen.add(target["address"])
                    targets.append(target)
    return targets, "; ".join(errors)


def _application_balancers(client: YandexCloudClient) -> tuple[list[dict[str, Any]], str, str]:
    """Return application load balancers with per-target health, and the next-page token.

    Unlike the network balancer's single ``:getTargetStates`` action, the
    application one keeps target states behind a nested path that needs the
    balancer's backend-group graph walked first (see ``_alb_target_health``).
    Walking it is what lets a failing application backend reach the same
    ``unhealthy_targets`` summary the network balancer feeds.
    """
    listed = client.get(_ALB_SERVICE, _ALB_PATH, {"folderId": client.folder_id})
    if not listed.get("success"):
        return [], str(listed.get("error", "")), ""
    more = str((listed.get("metadata") or {}).get("next_page_token", ""))

    balancers: list[dict[str, Any]] = []
    for balancer in (listed.get("data") or {}).get("loadBalancers") or []:
        lb_id = balancer.get("id", "")
        targets, error = _alb_target_health(client, lb_id, balancer)
        balancers.append(
            {
                "id": lb_id,
                "name": balancer.get("name", ""),
                "type": TYPE_APPLICATION,
                "status": balancer.get("status", ""),
                "listeners": len(balancer.get("listeners") or []),
                "targets": targets,
                "target_states_error": error,
            }
        )
    return balancers, "", more


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
        "balancers": "each balancer with its status and per-target health",
        "unhealthy_targets": "targets failing their health check, across network and application balancers",
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
    # A folder with more balancers than one page holds would otherwise lose the
    # rest without a word, and "no unhealthy targets" is exactly the answer that
    # must never be a guess.
    incomplete: list[str] = []

    if wanted in ("", TYPE_NETWORK):
        found, error, more = _network_balancers(client)
        balancers.extend(found)
        if error:
            errors.append(f"network: {error}")
        if more:
            incomplete.append(TYPE_NETWORK)

    if wanted in ("", TYPE_APPLICATION):
        found, error, more = _application_balancers(client)
        balancers.extend(found)
        if error:
            errors.append(f"application: {error}")
        if more:
            incomplete.append(TYPE_APPLICATION)

    unhealthy = [
        # An application balancer can come back without a name; fall back to its
        # id so an unhealthy target is never reported against a blank owner.
        {"balancer": balancer.get("name") or balancer.get("id", ""), **target}
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
    if incomplete:
        result["complete"] = False
        result["note"] = (
            f"More {' and '.join(incomplete)} balancers exist than this page holds, so "
            "an absent target is not evidence of a healthy one. Narrow the read with "
            "type, or list the rest with execute_yc_operation."
        )
    if errors and not balancers:
        result["available"] = False
        result["error"] = "; ".join(errors)
    elif errors:
        partial = "Partial results: " + "; ".join(errors)
        result["note"] = f"{result['note']} {partial}" if incomplete else partial
    return result


__all__ = ["get_yc_lb_health"]
