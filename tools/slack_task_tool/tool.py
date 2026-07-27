"""Slack to GitHub task management tools."""

from __future__ import annotations

import logging
from typing import Any

from core.tool_framework.tool_decorator import tool
from integrations.github.client import GitHubApiError, GitHubRestClient
from integrations.github.helpers import github_creds, github_source_available

logger = logging.getLogger(__name__)


def _slack_github_tool_available(sources: dict[str, dict]) -> bool:
    return github_source_available(sources)


def _slack_github_tool_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources.get("github", {})
    return github_creds(gh)


def _extract_slack_url(context: Any) -> str:
    """Safely extract the Slack thread or message URL from the runtime context."""
    if context is None:
        return ""

    try:
        session = getattr(context, "session", None)
        if session:
            source = getattr(session, "source", None)
            if source and getattr(source, "url", None):
                return str(source.url)
            metadata = getattr(session, "investigation_metadata", None)
            if metadata and getattr(metadata, "source_url", None):
                return str(metadata.source_url)

        resources = getattr(context, "resources", {})
        if isinstance(resources, dict):
            chat_ctx = resources.get("chat_context") or {}
            if isinstance(chat_ctx, dict) and chat_ctx.get("message_url"):
                return str(chat_ctx["message_url"])
    except Exception as exc:
        logger.debug("Failed to extract Slack URL from context", exc_info=exc)

    return ""


def _append_slack_source(body: str, source_url: str) -> str:
    """Append a Slack source link to the bottom of the issue body."""
    body = body or ""
    if not source_url:
        return body
    if body:
        return f"{body}\n\n---\n*Requested from Slack: {source_url}*"
    return f"*Requested from Slack: {source_url}*"


def _safe_github_request(
    client: GitHubRestClient, method: str, path: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Execute a GitHub API request with strict error boundaries and type checking."""
    try:
        response = client.request(method, path, body=payload)
    except GitHubApiError as exc:
        logger.warning("GitHub API error on %s %s: %s", method, path, exc)
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        logger.warning("Internal client error on %s %s: %s", method, path, exc)
        return {"ok": False, "error": f"Internal client error: {exc}"}

    if not isinstance(response, dict):
        return {"ok": False, "error": f"Unexpected GitHub API response type: {type(response)}"}

    return {"ok": True, "data": response}


@tool(
    name="create_github_task_from_slack",
    source="github",
    description="Create a new GitHub issue representing a task requested in Slack.",
    use_cases=[
        "Turning a Slack message 'add this to the hackathon list' into a GitHub issue",
        "Creating a tracking issue from a Slack conversation",
    ],
    surfaces=("chat",),
    side_effect_level="mutating",
    accepts_runtime_context=True,
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string", "description": "GitHub repository owner"},
            "repo": {"type": "string", "description": "GitHub repository name"},
            "title": {"type": "string", "description": "Title of the issue"},
            "body": {"type": "string", "description": "Description of the issue"},
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of labels",
            },
            "assignees": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of assignees",
            },
            "milestone": {
                "type": "integer",
                "description": "Optional milestone number to associate with this issue",
            },
            "project_number": {
                "type": "integer",
                "description": "Optional GitHub Projects V2 number to sync this issue to",
            },
            "project_owner": {
                "type": "string",
                "description": "Optional owner of the project if different from repo owner",
            },
            "project_fields": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Optional key-value map of Project fields to sync (e.g. {'Status': 'Todo'})",
            },
            "github_token": {"type": "string", "description": "Optional GitHub token override"},
        },
        "required": ["owner", "repo", "title"],
        "additionalProperties": False,
    },
    is_available=_slack_github_tool_available,
    extract_params=_slack_github_tool_extract_params,
)
def create_github_task_from_slack(
    owner: str,
    repo: str,
    title: str,
    body: str = "",
    labels: list[str] | None = None,
    assignees: list[str] | None = None,
    milestone: int | None = None,
    project_number: int | None = None,
    project_owner: str | None = None,
    project_fields: dict[str, str] | None = None,
    github_token: str | None = None,
    context: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Create a new GitHub issue and append the Slack message URL to the body."""
    client = GitHubRestClient(github_token=github_token)

    source_url = _extract_slack_url(context)
    final_body = _append_slack_source(body, source_url)

    payload: dict[str, Any] = {
        "title": title,
        "body": final_body,
    }
    if labels:
        payload["labels"] = labels
    if assignees:
        payload["assignees"] = assignees
    if milestone is not None:
        payload["milestone"] = milestone

    result = _safe_github_request(client, "POST", f"repos/{owner}/{repo}/issues", payload)
    if not result["ok"]:
        return result

    data = result["data"]

    project_synced = False
    if project_number is not None:
        from integrations.github.projects_v2 import sync_project_fields

        issue_node_id = data.get("node_id")
        if issue_node_id:
            try:
                project_synced = sync_project_fields(
                    client,
                    project_owner or owner,
                    project_number,
                    str(issue_node_id),
                    project_fields or {},
                )
            except Exception as exc:
                logger.warning("Project sync failed for issue %s: %s", issue_node_id, exc)

    response_payload = {
        "ok": True,
        "issue_number": data.get("number"),
        "issue_url": data.get("html_url"),
        "title": data.get("title"),
        "state": data.get("state"),
    }

    if project_number is not None:
        response_payload["project_synced"] = project_synced

    return response_payload


@tool(
    name="update_github_task_from_slack",
    source="github",
    description="Update an existing GitHub issue representing a Slack task.",
    use_cases=[
        "Updating issue labels or assignees based on conversational context",
        "Adding additional context to a previously created task",
    ],
    surfaces=("chat",),
    side_effect_level="mutating",
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string", "description": "GitHub repository owner"},
            "repo": {"type": "string", "description": "GitHub repository name"},
            "issue_number": {"type": "integer", "description": "The number of the issue to update"},
            "state": {
                "type": "string",
                "enum": ["open", "closed"],
                "description": "Optional state to set (open or closed)",
            },
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of labels (replaces existing labels)",
            },
            "assignees": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of assignees (replaces existing assignees)",
            },
            "project_number": {
                "type": "integer",
                "description": "Optional GitHub Projects V2 number to sync this issue to",
            },
            "project_owner": {
                "type": "string",
                "description": "Optional owner of the project if different from repo owner",
            },
            "project_fields": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Optional key-value map of Project fields to sync (e.g. {'Status': 'Done'})",
            },
            "github_token": {"type": "string", "description": "Optional GitHub token override"},
        },
        "required": ["owner", "repo", "issue_number"],
        "additionalProperties": False,
    },
    is_available=_slack_github_tool_available,
    extract_params=_slack_github_tool_extract_params,
)
def update_github_task_from_slack(
    owner: str,
    repo: str,
    issue_number: int,
    state: str | None = None,
    labels: list[str] | None = None,
    assignees: list[str] | None = None,
    project_number: int | None = None,
    project_owner: str | None = None,
    project_fields: dict[str, str] | None = None,
    github_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Update an existing GitHub issue using PATCH."""

    if all(v is None for v in [state, labels, assignees, project_number, project_fields]):
        return {"ok": False, "error": "No update fields provided."}

    if project_fields is not None and project_number is None:
        return {"ok": False, "error": "project_number is required to sync project_fields."}

    client = GitHubRestClient(github_token=github_token)

    payload: dict[str, Any] = {}
    if state is not None:
        payload["state"] = state
    if labels is not None:
        payload["labels"] = labels
    if assignees is not None:
        payload["assignees"] = assignees

    # Only fire the REST API patch if there is payload data for the issue itself.
    if payload:
        result = _safe_github_request(
            client, "PATCH", f"repos/{owner}/{repo}/issues/{issue_number}", payload
        )
        if not result["ok"]:
            return result
        data = result["data"]
    else:
        # Fetch the issue node_id using GET if we only received project updates.
        result = _safe_github_request(
            client, "GET", f"repos/{owner}/{repo}/issues/{issue_number}", {}
        )
        if not result["ok"]:
            return result
        data = result["data"]

    project_synced = False
    if project_number is not None:
        # User might only provide project_fields, assuming the issue is already in the project.
        # sync_project_fields requires project_number, so it must be passed in the slack payload.
        from integrations.github.projects_v2 import sync_project_fields

        issue_node_id = data.get("node_id")
        if issue_node_id:
            try:
                project_synced = sync_project_fields(
                    client,
                    project_owner or owner,
                    project_number,
                    str(issue_node_id),
                    project_fields or {},
                )
            except Exception as exc:
                logger.warning("Project sync failed for issue %s: %s", issue_node_id, exc)

    response_payload = {
        "ok": True,
        "issue_number": data.get("number"),
        "issue_url": data.get("html_url"),
        "state": data.get("state"),
        "labels": [
            label.get("name") for label in data.get("labels", []) if isinstance(label, dict)
        ],
        "assignees": [a.get("login") for a in data.get("assignees", []) if isinstance(a, dict)],
    }

    if project_number is not None:
        response_payload["project_synced"] = project_synced

    return response_payload


@tool(
    name="close_github_task_from_slack",
    source="github",
    description="Close a GitHub issue from Slack.",
    use_cases=[
        "Closing an issue when a Slack conversation declares a task shipped",
    ],
    surfaces=("chat",),
    side_effect_level="mutating",
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string", "description": "GitHub repository owner"},
            "repo": {"type": "string", "description": "GitHub repository name"},
            "issue_number": {"type": "integer", "description": "The number of the issue to close"},
            "github_token": {"type": "string", "description": "Optional GitHub token override"},
        },
        "required": ["owner", "repo", "issue_number"],
        "additionalProperties": False,
    },
    is_available=_slack_github_tool_available,
    extract_params=_slack_github_tool_extract_params,
)
def close_github_task_from_slack(
    owner: str,
    repo: str,
    issue_number: int,
    github_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Close an existing GitHub issue."""
    client = GitHubRestClient(github_token=github_token)
    payload: dict[str, Any] = {"state": "closed"}

    result = _safe_github_request(
        client, "PATCH", f"repos/{owner}/{repo}/issues/{issue_number}", payload
    )
    if not result["ok"]:
        return result

    data = result["data"]
    return {
        "ok": True,
        "issue_number": data.get("number"),
        "issue_url": data.get("html_url"),
        "state": data.get("state"),
    }
