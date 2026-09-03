"""Reading Cloud Logging during an investigation."""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.yandex_cloud.availability import (
    YC_INJECTED_PARAMS,
    config_from_params,
    yc_available_or_backend,
    yc_credentials,
)
from integrations.yandex_cloud.logging_client import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_WINDOW_MINUTES,
    LEVELS,
    LogReadingUnavailableError,
    YandexLoggingClient,
    resolve_window,
    retention_warning,
    validate_filter,
)

SOURCE = "yandex_cloud"

#: Attached to an empty read, where the wrong conclusion is easiest to draw.
#: Cloud Logging holds what was sent to it, which is not everything that was
#: logged, so "no entries" is a statement about this store and not about the
#: service being asked after.
_WHERE_ELSE_LOGS_LIVE = (
    "No entries in Cloud Logging for this window. That is not evidence nothing "
    "logged: a managed database keeps its own log and sends nothing here unless "
    "export was switched on, so read that one with read_yc_db_logs. Kubernetes "
    "container logs are read with "
    "kubernetes_get_pod_logs. Serverless functions do log here by default, so "
    "for those an empty result is meaningful. Check the window too: "
    "window_minutes counts back from now, and a past incident needs since "
    "and until."
)

_FILTER_HELP = (
    "Cloud Logging filter expression. Fields: message (the default, so bare "
    "text searches it), level, resource_type, resource_id, stream_name, "
    "json_payload.<key>. Operators: : contains, = equals, <> not equals, "
    "comparisons, EXISTS. Combine with AND / OR / NOT. Examples: "
    "'level >= WARN', 'message: \"connection refused\"', "
    "'json_payload.request_id = \"abc\"'. Max 1000 characters."
)


def _logging_client(params: dict[str, Any]) -> YandexLoggingClient | None:
    """Build the client from injected credentials, or None when unusable."""
    config = config_from_params(params)
    return None if config is None else YandexLoggingClient(config)


def _extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    params = yc_credentials(sources)
    # Alert context often names the log group; pass it through as the default.
    params["log_group_id"] = sources.get(SOURCE, {}).get("log_group_id", "")
    return params


@tool(
    name="read_yc_logs",
    surfaces=(ToolSurface.ACTION,),
    display_name="Cloud Logging",
    source=SOURCE,
    description=(
        "Read log entries from a Yandex Cloud Logging group. Filter by level, "
        "time window, resource, or a filter expression. Call list_yc_log_groups "
        "first if the log group id is not already known. Entries are retained "
        "for 31 days."
    ),
    use_cases=[
        "Pulling error and warning entries around the time an alert fired",
        "Finding a stack trace or error message behind a failing service",
        "Following a request or correlation id across a service's logs",
        "Checking whether a service logged anything at all during an outage",
    ],
    requires=["log_group_id"],
    outputs={
        "entries": "matching log entries, newest page first",
        "entry_count": "how many entries this page returned",
        "next_page_token": "token for the following page, when there is one",
        "note": "retention or truncation caveats worth knowing",
    },
    input_schema={
        "type": "object",
        "properties": {
            "log_group_id": {
                "type": "string",
                "description": "Log group to read from.",
            },
            "filter": {"type": "string", "description": _FILTER_HELP, "default": ""},
            "levels": {
                "type": "array",
                "items": {"type": "string", "enum": list(LEVELS)},
                "description": "Only return entries at these levels.",
                "default": [],
            },
            "since": {
                "type": "string",
                "description": "Start of the window, RFC3339. Defaults to window_minutes ago.",
                "default": "",
            },
            "until": {
                "type": "string",
                "description": "End of the window, RFC3339. Defaults to now.",
                "default": "",
            },
            "window_minutes": {
                "type": "integer",
                "description": "Window size when since is omitted.",
                "default": DEFAULT_WINDOW_MINUTES,
            },
            "resource_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Restrict to resource types, e.g. 'serverless.function'.",
                "default": [],
            },
            "resource_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Restrict to specific resource ids.",
                "default": [],
            },
            "page_size": {
                "type": "integer",
                "description": "Entries per page.",
                "default": DEFAULT_PAGE_SIZE,
            },
            "page_token": {
                "type": "string",
                "description": "Token from a previous call's next_page_token.",
                "default": "",
            },
        },
        "required": ["log_group_id"],
    },
    is_available=yc_available_or_backend,
    extract_params=_extract_params,
    injected_params=YC_INJECTED_PARAMS,
)
def read_yc_logs(
    log_group_id: str,
    filter: str = "",  # noqa: A002 - schema-facing name, matches how the model reads it
    levels: list[str] | None = None,
    since: str = "",
    until: str = "",
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    resource_types: list[str] | None = None,
    resource_ids: list[str] | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    page_token: str = "",
    yc_backend: Any = None,
    **credentials: Any,
) -> dict[str, Any]:
    """Read log entries from a Cloud Logging group."""
    if not log_group_id.strip():
        return tool_unavailable(
            SOURCE, "log_group_id is required. Call list_yc_log_groups to find one."
        )

    rejected = validate_filter(filter)
    if rejected is not None:
        return {"source": SOURCE, "available": False, "error": rejected}

    start, end = resolve_window(since, until, window_minutes)

    if yc_backend is not None:
        response = dict(
            yc_backend.read_yc_logs(log_group_id, filter, tuple(levels or []), start, end)
        )
    else:
        client = _logging_client(credentials)
        if client is None:
            return tool_unavailable(SOURCE, "Yandex Cloud credentials are not configured.")
        try:
            response = client.read_entries(
                log_group_id,
                since=start,
                until=end,
                levels=tuple(levels or []),
                filter_expression=filter,
                resource_types=tuple(resource_types or []),
                resource_ids=tuple(resource_ids or []),
                page_size=page_size,
                page_token=page_token,
            )
        except LogReadingUnavailableError as exc:
            return tool_unavailable(SOURCE, str(exc))

    if not response.get("success"):
        return {
            "source": SOURCE,
            "available": False,
            "error": response.get("error", "Log read failed."),
            "log_group_id": log_group_id,
        }

    entries = response.get("entries", [])
    result: dict[str, Any] = {
        "source": SOURCE,
        "available": True,
        "log_group_id": log_group_id,
        "window": {"since": start.isoformat(), "until": end.isoformat()},
        "entry_count": len(entries),
        "entries": entries,
        "next_page_token": response.get("next_page_token", ""),
    }
    note = retention_warning(start)
    if note:
        result["note"] = note
    if not entries:
        result["where_else_logs_live"] = _WHERE_ELSE_LOGS_LIVE
    return result


@tool(
    name="list_yc_log_groups",
    surfaces=(ToolSurface.ACTION,),
    display_name="Cloud Logging",
    source=SOURCE,
    description=(
        "List the Cloud Logging groups in the folder. Call before read_yc_logs "
        "to find the log group id for a service."
    ),
    use_cases=[
        "Finding the log group id for a service before reading its logs",
        "Checking which components in the folder log at all",
        "Seeing a group's retention period before querying an older window",
    ],
    requires=[],
    outputs={
        "log_groups": "log groups with id, name, retention, and status",
        "count": "how many groups were returned",
    },
    input_schema={
        "type": "object",
        "properties": {
            "page_token": {
                "type": "string",
                "description": "Token from a previous call's next_page_token.",
                "default": "",
            }
        },
        "required": [],
    },
    is_available=yc_available_or_backend,
    extract_params=_extract_params,
    injected_params=YC_INJECTED_PARAMS,
)
def list_yc_log_groups(
    page_token: str = "",
    yc_backend: Any = None,
    **credentials: Any,
) -> dict[str, Any]:
    """List the folder's Cloud Logging groups."""
    if yc_backend is not None:
        response = dict(yc_backend.list_yc_log_groups(page_token))
    else:
        client = _logging_client(credentials)
        if client is None:
            return tool_unavailable(SOURCE, "Yandex Cloud credentials are not configured.")
        response = client.list_log_groups(page_token=page_token)

    if not response.get("success"):
        return {
            "source": SOURCE,
            "available": False,
            "error": response.get("error", "Could not list log groups."),
        }

    groups = (response.get("data") or {}).get("groups") or []
    return {
        "source": SOURCE,
        "available": True,
        "log_groups": [
            {
                "id": group.get("id", ""),
                "name": group.get("name", ""),
                "status": group.get("status", ""),
                "retention_period": group.get("retentionPeriod", ""),
                "description": group.get("description", ""),
            }
            for group in groups
        ],
        "count": len(groups),
        "next_page_token": response.get("metadata", {}).get("next_page_token", ""),
    }


__all__ = ["list_yc_log_groups", "read_yc_logs"]
