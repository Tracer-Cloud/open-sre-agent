"""Generic read access to any Yandex Cloud service.

Yandex Cloud has north of a hundred services. Giving each one a dedicated tool
would blow the per-investigation schema budget many times over, so the ones
that matter most for incident response get purpose-built tools and everything
else is reachable through this pair: one to discover what services exist, one
to read from any of them.

Reads only. The client refuses anything but GET, which across Yandex's
uniformly generated REST API means list and get and nothing that mutates.
"""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.yandex_cloud.api_index import canonical_service, known_services, lookup
from integrations.yandex_cloud.availability import (
    YC_INJECTED_PARAMS,
    client_from_params,
    yc_available_or_backend,
    yc_credentials,
)
from integrations.yandex_cloud.rest_client import (
    DEFAULT_PAGE_SIZE,
    accepts_folder_scope,
    is_collection_path,
    is_folder_scoped_collection,
    reject_path,
)

SOURCE = "yandex_cloud"

# Kept out of the list literal below: two adjacent strings inside a list display
# are indistinguishable from a missing comma, and this text is long enough to
# need wrapping.
_WORKLOAD_ANTI_EXAMPLE = (
    "Reading Kubernetes pods, events or container logs. Those live in the "
    "cluster's own API server, not in Yandex Cloud's, so no path here returns "
    "them and searching for one only burns the investigation. Use "
    "kubernetes_list_pods, kubernetes_get_events and kubernetes_get_pod_logs."
)


def _extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    return yc_credentials(sources)


@tool(
    name="execute_yc_operation",
    surfaces=(ToolSurface.INVESTIGATION, ToolSurface.ACTION),
    display_name="Yandex Cloud",
    source=SOURCE,
    description=(
        "Read any Yandex Cloud resource through the REST API. Use for services "
        "without a dedicated tool — container registry, DNS, CDN, IAM, KMS, "
        "Lockbox, certificates, YDB, Data Transfer and the rest. Call "
        "find_yc_api first if you are unsure of the service name or path. "
        "Read-only: only list and get endpoints can be reached. When the "
        "investigation calls for a change, report the exact `yc` command for an "
        "operator to run rather than attempting it. Reaches Yandex Cloud "
        "resources only: a Kubernetes pod, event or container log is not one, "
        "and no path here returns them."
    ),
    use_cases=[
        "Inspecting a service that has no dedicated tool",
        "Listing resources in a folder to establish what exists",
        "Fetching one resource by id to check its current state",
        "Following a resource reference found in another tool's output",
    ],
    anti_examples=[_WORKLOAD_ANTI_EXAMPLE],
    requires=["service", "path"],
    outputs={
        "success": "whether the read succeeded",
        "data": "the API response, with long lists and strings truncated",
        "error": "why the read failed, including any permission hint",
        "metadata": "status code, request id, and next_page_token when more pages exist",
    },
    input_schema={
        "type": "object",
        "properties": {
            "service": {
                "type": "string",
                "description": (
                    "Yandex Cloud service id, e.g. 'compute', 'vpc', "
                    "'mdb-postgresql', 'container-registry'."
                ),
            },
            "path": {
                "type": "string",
                "description": (
                    "REST path starting with '/', e.g. '/compute/v1/instances' or "
                    "'/compute/v1/instances/{id}'."
                ),
            },
            "params": {
                "type": "object",
                "description": (
                    "Query parameters. Most list endpoints need folderId; the "
                    "configured folder is used when it is omitted."
                ),
                "additionalProperties": {"type": "string"},
                "default": {},
            },
            "page_token": {
                "type": "string",
                "description": "Token from a previous call's next_page_token.",
                "default": "",
            },
        },
        "required": ["service", "path"],
    },
    is_available=yc_available_or_backend,
    extract_params=_extract_params,
    injected_params=YC_INJECTED_PARAMS,
)
def execute_yc_operation(
    service: str,
    path: str,
    params: dict[str, Any] | None = None,
    page_token: str = "",
    yc_backend: Any = None,
    **credentials: Any,
) -> dict[str, Any]:
    """Read a Yandex Cloud resource."""
    # Checked here rather than only in the client: the synthetic-backend path
    # never reaches the client, and a read-only guarantee that depends on which
    # branch the call took is not a guarantee.
    rejected = reject_path(path)
    if rejected is not None:
        return {
            "success": False,
            "service": service,
            "path": path,
            "data": None,
            "error": f"Invalid path: {rejected}.",
            "metadata": {},
        }
    # The index is the GET-only allowlist. Path-shape checks above catch
    # traversal and mutating suffixes; they do not stop a well-formed write
    # path that Yandex binds to GET, or a path the generator never listed.
    # Unknown services still fall through to the client, which names them as
    # unknown. A known service with a path the index does not list is refused
    # here so a well-formed write never reaches the network. Canonicalise first:
    # the caller may use the name the tools and the registry use, and asking the
    # index about a name it does not carry would read as "unknown service" and
    # skip the check.
    if canonical_service(service) in known_services() and lookup(service, path) is None:
        return {
            "success": False,
            "service": service,
            "path": path,
            "data": None,
            "error": (
                f"'{path}' is not a documented read of '{service}'. "
                "Call find_yc_api to get the path; this tool only sends what "
                "the index lists."
            ),
            "metadata": {},
        }

    query: dict[str, Any] = dict(params or {})
    # Both of the things this tool fills in for the caller are rejected by a
    # single-resource read, and Yandex reports that as a bare 404 — so adding
    # them unconditionally makes every existing resource look missing.
    collection = is_collection_path(path)
    # Paging is safe on any collection, but the folder only scopes the ones
    # directly under the version: a nested collection is already scoped by the
    # resource named in its path, and adding a folder there 404s the same silent
    # way. A caller that passed cloudId is scoping the read some other way, so
    # leave that alone too.
    wants_folder = (
        is_folder_scoped_collection(path)
        and accepts_folder_scope(service)
        and "cloudId" not in query
    )

    if yc_backend is not None:
        folder_id = str(credentials.get("folder_id", "") or "")
        if wants_folder and folder_id and "folderId" not in query:
            query["folderId"] = folder_id
        return dict(yc_backend.execute_yc_operation(service, path, query, page_token))

    client = client_from_params(credentials)
    if client is None:
        return tool_unavailable(SOURCE, "Yandex Cloud credentials are not configured.")
    # Ask the client rather than the raw credentials: on an instance the folder
    # is discovered from the metadata service and is not in them at all. Most
    # list endpoints reject a request without it.
    if wants_folder and "folderId" not in query and client.folder_id:
        query["folderId"] = client.folder_id
    return client.get(
        service,
        path,
        query,
        page_token=page_token,
        page_size=DEFAULT_PAGE_SIZE if collection else None,
    )
