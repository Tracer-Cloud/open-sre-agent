"""Inspecting Compute Cloud instances during an investigation."""

from __future__ import annotations

from typing import Any

from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.yandex_cloud.availability import (
    YC_INJECTED_PARAMS,
    client_from_params,
    yc_available_or_backend,
    yc_credentials,
)

SOURCE = "yandex_cloud"
SERVICE = "compute"

_INSTANCES_PATH = "/compute/v1/instances"

#: Serial console output is the one place a VM that will not boot or has lost
#: its network still talks, so it is worth a tool of its own.
_SERIAL_PORT_SUFFIX = ":serialPortOutput"

#: Statuses that mean the instance is not serving, worth surfacing on their own.
_UNHEALTHY_STATUSES = frozenset(
    {"STOPPED", "STOPPING", "ERROR", "CRASHED", "PROVISIONING", "STARTING", "RESTARTING"}
)


def _extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    return yc_credentials(sources)


def _summarize(instance: dict[str, Any]) -> dict[str, Any]:
    resources = instance.get("resources") or {}
    interfaces = instance.get("networkInterfaces") or []
    addresses = [
        (interface.get("primaryV4Address") or {}).get("address", "")
        for interface in interfaces
        if (interface.get("primaryV4Address") or {}).get("address")
    ]
    public = [
        ((interface.get("primaryV4Address") or {}).get("oneToOneNat") or {}).get("address", "")
        for interface in interfaces
        if ((interface.get("primaryV4Address") or {}).get("oneToOneNat") or {}).get("address")
    ]
    return {
        "id": instance.get("id", ""),
        "name": instance.get("name", ""),
        "status": instance.get("status", ""),
        "zone": instance.get("zoneId", ""),
        "platform": instance.get("platformId", ""),
        "cores": resources.get("cores", ""),
        "memory": resources.get("memory", ""),
        "created_at": instance.get("createdAt", ""),
        "private_addresses": addresses,
        "public_addresses": public,
        "labels": instance.get("labels", {}),
    }


@tool(
    name="list_yc_instances",
    display_name="Compute Cloud",
    source=SOURCE,
    description=(
        "List Compute Cloud instances in the folder with their status, zone, "
        "size, and addresses. Use to establish what is running, find an "
        "instance by name or label, or spot instances that are stopped or in "
        "error while others are fine."
    ),
    use_cases=[
        "Finding an instance id from a hostname or IP address seen in an alert",
        "Checking whether an instance is running, stopped, or in error",
        "Spotting a single unhealthy VM against an otherwise healthy fleet",
        "Establishing the size of an instance before judging a saturation metric",
    ],
    requires=[],
    outputs={
        "instances": "instances with status, zone, size, and addresses",
        "unhealthy": "the subset that is not RUNNING",
        "count": "how many instances were returned",
    },
    input_schema={
        "type": "object",
        "properties": {
            "name_filter": {
                "type": "string",
                "description": "Only return instances whose name contains this text.",
                "default": "",
            },
            "page_token": {
                "type": "string",
                "description": "Token from a previous call's next_page_token.",
                "default": "",
            },
        },
        "required": [],
    },
    is_available=yc_available_or_backend,
    extract_params=_extract_params,
    injected_params=YC_INJECTED_PARAMS,
)
def list_yc_instances(
    name_filter: str = "",
    page_token: str = "",
    yc_backend: Any = None,
    **credentials: Any,
) -> dict[str, Any]:
    """List Compute Cloud instances."""
    if yc_backend is not None:
        response = dict(yc_backend.list_yc_instances(name_filter, page_token))
    else:
        client = client_from_params(credentials)
        if client is None:
            return tool_unavailable(SOURCE, "Yandex Cloud credentials are not configured.")
        response = client.get(
            SERVICE,
            _INSTANCES_PATH,
            {"folderId": client.folder_id},
            page_token=page_token,
        )

    if not response.get("success"):
        return {
            "source": SOURCE,
            "available": False,
            "error": response.get("error", "Could not list instances."),
        }

    raw = (response.get("data") or {}).get("instances") or []
    instances = [_summarize(instance) for instance in raw]
    # Yandex's own filter matches a name exactly (=, !=, IN, NOT IN — no
    # substring), so narrowing by a fragment has to happen here. That makes the
    # filter local to this page: on a later page a match would go unseen.
    needle = name_filter.strip().lower()
    if needle:
        instances = [item for item in instances if needle in str(item["name"]).lower()]

    next_page_token = response.get("metadata", {}).get("next_page_token", "")
    result: dict[str, Any] = {
        "source": SOURCE,
        "available": True,
        "instances": instances,
        "unhealthy": [item for item in instances if item["status"] in _UNHEALTHY_STATUSES],
        "count": len(instances),
        "next_page_token": next_page_token,
    }
    if needle and next_page_token:
        result["note"] = (
            f"'{name_filter}' was matched against this page only, and further pages "
            "exist. Pass page_token to keep looking before concluding no instance "
            "matches."
        )
    return result


@tool(
    name="get_yc_instance_diagnostics",
    display_name="Compute Cloud",
    source=SOURCE,
    description=(
        "Fetch one instance's full configuration and its serial console output. "
        "The serial console is where a VM that will not boot, has lost its "
        "network, or is out of disk still reports what is wrong — reach for it "
        "when an instance is unreachable but the API says it is running."
    ),
    use_cases=[
        "Diagnosing a VM that is running but unreachable over the network",
        "Reading kernel or boot errors from a failing instance",
        "Checking an instance's disks, interfaces, and metadata in one call",
        "Confirming an out-of-memory kill or filesystem error on a host",
    ],
    requires=["instance_id"],
    outputs={
        "instance": "the instance's configuration",
        "serial_port_output": "recent serial console output, when available",
        "serial_port_error": "why the console could not be read, when it could not",
    },
    input_schema={
        "type": "object",
        "properties": {
            "instance_id": {
                "type": "string",
                "description": "Instance id, as returned by list_yc_instances.",
            },
            "include_serial_output": {
                "type": "boolean",
                "description": "Also read the serial console.",
                "default": True,
            },
        },
        "required": ["instance_id"],
    },
    is_available=yc_available_or_backend,
    extract_params=_extract_params,
    injected_params=YC_INJECTED_PARAMS,
)
def get_yc_instance_diagnostics(
    instance_id: str,
    include_serial_output: bool = True,
    yc_backend: Any = None,
    **credentials: Any,
) -> dict[str, Any]:
    """Read one instance's configuration and serial console output."""
    if not instance_id.strip():
        return tool_unavailable(
            SOURCE, "instance_id is required. Call list_yc_instances to find one."
        )

    if yc_backend is not None:
        return dict(yc_backend.get_yc_instance_diagnostics(instance_id, include_serial_output))

    client = client_from_params(credentials)
    if client is None:
        return tool_unavailable(SOURCE, "Yandex Cloud credentials are not configured.")

    detail = client.get(SERVICE, f"{_INSTANCES_PATH}/{instance_id}", page_size=None)
    if not detail.get("success"):
        return {
            "source": SOURCE,
            "available": False,
            "error": detail.get("error", "Could not read the instance."),
            "instance_id": instance_id,
        }

    result: dict[str, Any] = {
        "source": SOURCE,
        "available": True,
        "instance_id": instance_id,
        "instance": detail.get("data") or {},
    }

    if include_serial_output:
        serial = client.get(
            SERVICE,
            f"{_INSTANCES_PATH}/{instance_id}{_SERIAL_PORT_SUFFIX}",
            page_size=None,
        )
        if serial.get("success"):
            result["serial_port_output"] = (serial.get("data") or {}).get("contents", "")
        else:
            # A stopped instance has no console; that is worth saying rather
            # than failing the whole call.
            result["serial_port_error"] = serial.get("error", "")

    return result


__all__ = ["get_yc_instance_diagnostics", "list_yc_instances"]
