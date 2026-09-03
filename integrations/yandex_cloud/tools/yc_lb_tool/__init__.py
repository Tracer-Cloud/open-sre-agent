"""Load balancer target health — the bridge from a user-facing symptom to a host."""

from __future__ import annotations

from typing import Any, Final

from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.yandex_cloud.availability import (
    YC_INJECTED_PARAMS,
    client_from_params,
    yc_available_or_backend,
    yc_credentials,
)
from integrations.yandex_cloud.rest_client import YandexCloudClient

SOURCE = "yandex_cloud"

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
        targets, states_error = _network_target_states(client, balancer_id, balancer)
        balancers.append(
            {
                "id": balancer_id,
                "name": balancer.get("name", ""),
                "type": TYPE_NETWORK,
                "status": balancer.get("status", ""),
                "listeners": len(balancer.get("listeners") or []),
                "targets": targets,
                "target_states_error": states_error,
            }
        )
    return balancers, "", more


def _attached_target_group_ids(balancer: dict[str, Any]) -> list[str]:
    """Every target group id attached to a network load balancer."""
    ids: list[str] = []
    for attachment in balancer.get("attachedTargetGroups") or []:
        group_id = str(attachment.get("targetGroupId", "") or "")
        if group_id and group_id not in ids:
            ids.append(group_id)
    return ids


def _network_target_states(
    client: YandexCloudClient, balancer_id: str, balancer: dict[str, Any]
) -> tuple[list[dict[str, Any]], str]:
    """Target health across all attached groups — not only the first one."""
    group_ids = _attached_target_group_ids(balancer)
    if not group_ids:
        return [], ""

    targets: list[dict[str, Any]] = []
    errors: list[str] = []
    for group_id in group_ids:
        states = client.get(
            _NLB_SERVICE,
            f"{_NLB_PATH}/{balancer_id}:getTargetStates",
            {"targetGroupId": group_id},
            page_size=None,
        )
        if not states.get("success"):
            errors.append(f"{group_id}: {states.get('error', '')}")
            continue
        for target in _normalized_targets(states):
            targets.append({**target, "target_group": group_id})
    return targets, "; ".join(errors)


_ALB_HTTP_ROUTER_PATH = "/apploadbalancer/v1/httpRouters"
_ALB_BACKEND_GROUP_PATH = "/apploadbalancer/v1/backendGroups"
#: A router with more virtual hosts than this is pathological; the cap keeps a
#: paging bug from turning one investigation into an unbounded read, and running
#: into it is reported rather than passed off as a complete graph.
_MAX_VIRTUAL_HOST_PAGES: Final = 20


def _alb_handlers(balancer: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every handler a listener can carry, whatever its shape.

    A listener is http, tls or stream. The tls one nests a default handler and
    any number of SNI handlers, and each of those is either an httpHandler or a
    streamHandler. Missing a shape means the backends behind it are never
    checked, so the shapes are enumerated rather than guessed at.
    """
    handlers: list[dict[str, Any]] = []
    for listener in balancer.get("listeners") or []:
        http_handler = (listener.get("http") or {}).get("handler")
        if http_handler:
            handlers.append(http_handler)
        stream_handler = (listener.get("stream") or {}).get("handler")
        if stream_handler:
            handlers.append(stream_handler)
        tls = listener.get("tls") or {}
        nested = [tls.get("defaultHandler")] + [
            sni.get("handler") for sni in tls.get("sniHandlers") or []
        ]
        for entry in nested:
            if not entry:
                continue
            for key in ("httpHandler", "streamHandler"):
                inner = entry.get(key)
                if inner:
                    handlers.append(inner)
    return handlers


def _alb_router_ids(balancer: dict[str, Any]) -> list[str]:
    """Router ids from every handler that routes over HTTP."""
    ids = [
        handler["httpRouterId"]
        for handler in _alb_handlers(balancer)
        if handler.get("httpRouterId")
    ]
    return sorted(set(ids))


def _alb_listener_backend_group_ids(balancer: dict[str, Any]) -> list[str]:
    """Backend groups a listener names directly.

    A stream handler carries its backend group itself rather than going through
    an HTTP router, so walking routers alone never reaches it.
    """
    ids = [
        handler["backendGroupId"]
        for handler in _alb_handlers(balancer)
        if handler.get("backendGroupId")
    ]
    return sorted(set(ids))


def _alb_backend_group_ids(
    client: YandexCloudClient, router_ids: list[str]
) -> tuple[list[str], str]:
    """Return the backend groups the balancer's routes point at, and any read error.

    A route is either HTTP or gRPC, and each names its backend group under its
    own key. Reading only the HTTP form leaves a gRPC route's targets unchecked.

    A failed router read is reported rather than swallowed: an unreadable graph
    is indistinguishable from a balancer with no backends, and the difference is
    "no unhealthy targets" versus "we could not tell".
    """
    ids: list[str] = []
    errors: list[str] = []
    for router_id in router_ids:
        path = f"{_ALB_HTTP_ROUTER_PATH}/{router_id}/virtualHosts"
        page_token = ""
        # Every page is followed: a backend group reachable only from the second
        # page would otherwise contribute no targets, and the result would still
        # claim to be complete.
        for _ in range(_MAX_VIRTUAL_HOST_PAGES):
            resp = client.get(_ALB_SERVICE, path, page_token=page_token)
            if not resp.get("success"):
                errors.append(f"router {router_id}: {resp.get('error', '')}")
                break
            for vhost in (resp.get("data") or {}).get("virtualHosts") or []:
                for route in vhost.get("routes") or []:
                    for proto in ("http", "grpc"):
                        backend_group = (
                            (route.get(proto) or {}).get("route", {}).get("backendGroupId")
                        )
                        if backend_group:
                            ids.append(backend_group)
            page_token = str((resp.get("metadata") or {}).get("next_page_token", ""))
            if not page_token:
                break
        else:
            errors.append(f"router {router_id}: stopped after {_MAX_VIRTUAL_HOST_PAGES} pages")
    return sorted(set(ids)), "; ".join(errors)


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
    seen: set[tuple[str, str, str]] = set()
    routed_ids, router_error = _alb_backend_group_ids(client, _alb_router_ids(balancer))
    if router_error:
        errors.append(router_error)
    # Stream handlers name their backend group on the listener, so they are
    # merged in alongside the ones discovered through routers.
    backend_group_ids = sorted(set(routed_ids) | set(_alb_listener_backend_group_ids(balancer)))
    for bg_id in backend_group_ids:
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
                # Keyed by the relationship, not the address alone: one backend
                # can serve several target groups and be healthy in one while
                # failing in another, and collapsing by IP would let the healthy
                # record hide the failing one.
                key = (bg_id, tg_id, target["address"])
                if key in seen:
                    continue
                seen.add(key)
                targets.append({**target, "backend_group": bg_id, "target_group": tg_id})
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
    surfaces=(ToolSurface.ACTION,),
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

    # A balancer whose graph could not be read fully has no trustworthy target
    # list, and an empty unhealthy_targets from it means "we could not tell",
    # not "everything is healthy". Those per-balancer errors are promoted here
    # rather than left where only a caller reading each balancer would see them.
    unreadable = [
        balancer.get("name") or balancer.get("id", "")
        for balancer in balancers
        if balancer.get("target_states_error")
    ]

    result: dict[str, Any] = {
        "source": SOURCE,
        "available": True,
        "balancers": balancers,
        "unhealthy_targets": unhealthy,
        "count": len(balancers),
    }

    notes: list[str] = []
    if incomplete:
        notes.append(
            f"More {' and '.join(incomplete)} balancers exist than this page holds, so "
            "an absent target is not evidence of a healthy one. Narrow the read with "
            "type, or list the rest with execute_yc_operation."
        )
    if unreadable:
        notes.append(
            f"Target health could not be read for {', '.join(unreadable)}, so an empty "
            "unhealthy list is not evidence those targets are healthy. See "
            "target_states_error on each balancer."
        )
    if incomplete or unreadable:
        result["complete"] = False
    if errors and not balancers:
        result["available"] = False
        result["error"] = "; ".join(errors)
        return result
    if errors:
        notes.append("Partial results: " + "; ".join(errors))
    if notes:
        result["note"] = " ".join(notes)
    return result


__all__ = ["get_yc_lb_health"]
