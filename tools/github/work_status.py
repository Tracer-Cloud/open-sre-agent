"""GitHub work, PR, and security status tools."""

from __future__ import annotations

import json
import os
from typing import Any, cast
from urllib import error, parse, request

from tools.tool_decorator import tool
from tools.utils.github_helpers import github_creds, github_source_available

GitHubPayload = dict[str, Any] | list[Any]

_HELP_WANTED_LABELS = {"help wanted", "good first issue", "up for grabs", "agent-ready"}
_BLOCKING_MERGEABLE_STATES = {"blocked", "dirty", "behind", "unstable"}
_FAILED_CHECK_CONCLUSIONS = {
    "failure",
    "cancelled",
    "timed_out",
    "action_required",
    "startup_failure",
}


def _github_available(sources: dict[str, dict]) -> bool:
    gh = sources.get("github", {})
    return bool(
        (github_source_available(sources) or _github_token_from_env())
        and gh.get("owner")
        and gh.get("repo")
    )


def _github_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources.get("github", {})
    if not gh:
        return {}
    return {"owner": gh.get("owner"), "repo": gh.get("repo"), **github_creds(gh)}


def _github_token_from_env() -> str:
    return (os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip()


def _github_api_request(
    method: str,
    path: str,
    *,
    github_token: str | None = None,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> GitHubPayload:
    """Call the GitHub REST API and return parsed JSON.

    Kept intentionally small and stdlib-only so the higher-level tools are easy
    to unit-test by patching this single function.
    """

    token = (github_token or _github_token_from_env()).strip()
    if not token:
        raise RuntimeError(
            "GitHub token is required. Configure github_token, GITHUB_TOKEN, or GH_TOKEN."
        )

    query = f"?{parse.urlencode(params, doseq=True)}" if params else ""
    url = f"https://api.github.com{path}{query}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = request.Request(
        url,
        data=data,
        method=method.upper(),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with request.urlopen(req, timeout=20) as response:  # nosemgrep
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"GitHub API {method.upper()} {path} failed with HTTP {exc.code}: {detail}"
        ) from exc
    except error.URLError as exc:
        raise RuntimeError(f"GitHub API {method.upper()} {path} failed: {exc.reason}") from exc
    if not raw.strip():
        return {}
    parsed = json.loads(raw)
    return cast("GitHubPayload", parsed)


def _labels(item: dict[str, Any]) -> list[str]:
    return [
        str(label.get("name", "")).strip()
        for label in item.get("labels", [])
        if isinstance(label, dict)
    ]


def _logins(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    return [
        str(item.get("login", "")).strip()
        for item in items
        if isinstance(item, dict) and item.get("login")
    ]


def _normalize_issue(item: dict[str, Any]) -> dict[str, Any]:
    labels = _labels(item)
    assignees = _logins(item.get("assignees"))
    label_set = {label.lower() for label in labels}
    if assignees:
        work_status = "taken"
    elif label_set & _HELP_WANTED_LABELS:
        work_status = "up_for_grabs"
    else:
        work_status = "unassigned"
    return {
        "number": item.get("number"),
        "title": str(item.get("title", "")),
        "state": str(item.get("state", "")),
        "url": str(item.get("html_url", "")),
        "author": str((item.get("user") or {}).get("login", "")),
        "labels": labels,
        "assignees": assignees,
        "updated_at": str(item.get("updated_at", "")),
        "work_status": work_status,
    }


def _count_work_items(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(items),
        "taken": sum(1 for item in items if item.get("work_status") == "taken"),
        "up_for_grabs": sum(1 for item in items if item.get("work_status") == "up_for_grabs"),
        "unassigned": sum(1 for item in items if item.get("work_status") == "unassigned"),
    }


@tool(
    name="list_github_work_items",
    source="github",
    description="List GitHub issues as engineering work items and classify them as taken, up for grabs, or unassigned.",
    use_cases=[
        "Answering which GitHub issues are taken versus available",
        "Building engineering status reports from open issue state",
        "Finding unassigned or agent-ready work without mutating GitHub",
    ],
    anti_examples=["Creating, editing, or closing GitHub issues"],
    requires=["owner", "repo"],
    surfaces=("investigation", "chat"),
    side_effect_level="read_only",
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "state": {"type": "string", "enum": ["open", "closed", "all"]},
            "labels": {"type": "string"},
            "include_prs": {"type": "boolean"},
            "per_page": {"type": "integer"},
            "github_token": {"type": "string"},
        },
        "required": ["owner", "repo"],
    },
    is_available=_github_available,
    extract_params=_github_extract_params,
)
def list_github_work_items(
    owner: str,
    repo: str,
    state: str = "open",
    labels: str = "",
    include_prs: bool = False,
    per_page: int = 50,
    github_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    params: dict[str, Any] = {"state": state, "per_page": max(1, min(per_page, 100))}
    if labels.strip():
        params["labels"] = labels.strip()
    try:
        payload = _github_api_request(
            "GET",
            f"/repos/{owner}/{repo}/issues",
            github_token=github_token,
            params=params,
        )
    except RuntimeError as exc:
        return {
            "source": "github",
            "available": False,
            "error": str(exc),
            "items": [],
            "counts": _count_work_items([]),
        }
    raw_items = payload if isinstance(payload, list) else []
    issues = [
        _normalize_issue(item)
        for item in raw_items
        if isinstance(item, dict) and (include_prs or "pull_request" not in item)
    ]
    return {
        "source": "github",
        "available": True,
        "owner": owner,
        "repo": repo,
        "items": issues,
        "counts": _count_work_items(issues),
        "side_effects": [],
    }


def _check_summary(check_runs: list[dict[str, Any]]) -> tuple[str, list[str]]:
    failed = [
        str(run.get("name", "check"))
        for run in check_runs
        if str(run.get("conclusion") or "").lower() in _FAILED_CHECK_CONCLUSIONS
    ]
    pending = [
        str(run.get("name", "check"))
        for run in check_runs
        if str(run.get("status") or "").lower() != "completed"
    ]
    if failed:
        return "failed", failed
    if pending:
        return "pending", pending
    return "passing", []


def _normalize_pull_request(pr: dict[str, Any], check_runs: list[dict[str, Any]]) -> dict[str, Any]:
    check_status, check_names = _check_summary(check_runs)
    mergeable_state = str(pr.get("mergeable_state") or "").lower()
    reasons: list[str] = []
    if pr.get("draft"):
        reasons.append("draft")
    if mergeable_state in _BLOCKING_MERGEABLE_STATES:
        reasons.append(f"mergeable_state={mergeable_state}")
    if check_status == "failed":
        reasons.append(f"failed checks: {', '.join(check_names)}")
    elif check_status == "pending":
        reasons.append(f"pending checks: {', '.join(check_names)}")

    status = "blocked" if reasons else "mergeable"
    return {
        "number": pr.get("number"),
        "title": str(pr.get("title", "")),
        "url": str(pr.get("html_url", "")),
        "author": str((pr.get("user") or {}).get("login", "")),
        "head_ref": str((pr.get("head") or {}).get("ref", "")),
        "head_sha": str((pr.get("head") or {}).get("sha", "")),
        "draft": bool(pr.get("draft")),
        "mergeable": pr.get("mergeable"),
        "mergeable_state": mergeable_state,
        "check_status": check_status,
        "status": status,
        "blocking_reasons": reasons,
        "updated_at": str(pr.get("updated_at", "")),
    }


def _count_prs(prs: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(prs),
        "mergeable": sum(1 for pr in prs if pr.get("status") == "mergeable"),
        "blocked": sum(1 for pr in prs if pr.get("status") == "blocked"),
        "draft": sum(1 for pr in prs if pr.get("draft")),
    }


@tool(
    name="summarize_github_pr_status",
    source="github",
    description="Summarize open GitHub pull requests, mergeability, checks, and blocking reasons.",
    use_cases=[
        "Answering which PRs are mergeable or blocked",
        "Finding failing or pending CI checks for active work",
        "Preparing engineering status updates without changing GitHub state",
    ],
    requires=["owner", "repo"],
    surfaces=("investigation", "chat"),
    side_effect_level="read_only",
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "state": {"type": "string", "enum": ["open", "closed", "all"]},
            "per_page": {"type": "integer"},
            "include_checks": {"type": "boolean"},
            "github_token": {"type": "string"},
        },
        "required": ["owner", "repo"],
    },
    is_available=_github_available,
    extract_params=_github_extract_params,
)
def summarize_github_pr_status(
    owner: str,
    repo: str,
    state: str = "open",
    per_page: int = 30,
    include_checks: bool = True,
    github_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    try:
        payload = _github_api_request(
            "GET",
            f"/repos/{owner}/{repo}/pulls",
            github_token=github_token,
            params={"state": state, "per_page": max(1, min(per_page, 100))},
        )
        raw_prs = payload if isinstance(payload, list) else []
        prs: list[dict[str, Any]] = []
        for pr in raw_prs:
            if not isinstance(pr, dict):
                continue
            check_runs: list[dict[str, Any]] = []
            sha = str((pr.get("head") or {}).get("sha", ""))
            if include_checks and sha:
                check_payload = _github_api_request(
                    "GET",
                    f"/repos/{owner}/{repo}/commits/{sha}/check-runs",
                    github_token=github_token,
                    params={"per_page": 100},
                )
                if isinstance(check_payload, dict) and isinstance(
                    check_payload.get("check_runs"), list
                ):
                    check_runs = [
                        run for run in check_payload["check_runs"] if isinstance(run, dict)
                    ]
            prs.append(_normalize_pull_request(pr, check_runs))
    except RuntimeError as exc:
        return {
            "source": "github",
            "available": False,
            "error": str(exc),
            "pull_requests": [],
            "counts": _count_prs([]),
        }

    return {
        "source": "github",
        "available": True,
        "owner": owner,
        "repo": repo,
        "pull_requests": prs,
        "counts": _count_prs(prs),
        "side_effects": [],
    }


def _normalize_security_alert(alert_type: str, item: dict[str, Any]) -> dict[str, Any]:
    summary = ""
    if alert_type == "dependabot":
        summary = str((item.get("security_advisory") or {}).get("summary", ""))
    elif alert_type == "secret_scanning":
        summary = str(item.get("secret_type", ""))
    elif alert_type == "code_scanning":
        summary = str((item.get("rule") or {}).get("description", ""))
    return {
        "type": alert_type,
        "number": item.get("number"),
        "state": str(item.get("state", "")),
        "summary": summary,
        "url": str(item.get("html_url", "")),
    }


_ALERT_ENDPOINTS = {
    "dependabot": "dependabot/alerts",
    "secret_scanning": "secret-scanning/alerts",
    "code_scanning": "code-scanning/alerts",
}


@tool(
    name="list_github_security_alerts",
    source="github",
    description="List GitHub Dependabot, secret-scanning, and code-scanning alerts when token scope allows it.",
    use_cases=[
        "Surfacing repository security alerts during work triage",
        "Checking whether secret scanning or code scanning has open alerts",
        "Building a read-only engineering status report with security context",
    ],
    requires=["owner", "repo"],
    surfaces=("investigation", "chat"),
    side_effect_level="read_only",
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "alert_type": {
                "type": "string",
                "enum": ["all", "dependabot", "secret_scanning", "code_scanning"],
            },
            "state": {"type": "string"},
            "github_token": {"type": "string"},
        },
        "required": ["owner", "repo"],
    },
    is_available=_github_available,
    extract_params=_github_extract_params,
)
def list_github_security_alerts(
    owner: str,
    repo: str,
    alert_type: str = "all",
    state: str = "open",
    github_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    selected = list(_ALERT_ENDPOINTS) if alert_type == "all" else [alert_type]
    alerts: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for kind in selected:
        endpoint = _ALERT_ENDPOINTS.get(kind)
        if endpoint is None:
            errors[kind] = f"Unsupported alert_type: {kind}"
            continue
        try:
            payload = _github_api_request(
                "GET",
                f"/repos/{owner}/{repo}/{endpoint}",
                github_token=github_token,
                params={"state": state, "per_page": 100},
            )
        except RuntimeError as exc:
            errors[kind] = str(exc)
            continue
        if isinstance(payload, list):
            alerts.extend(
                _normalize_security_alert(kind, item) for item in payload if isinstance(item, dict)
            )
    counts = {
        kind: sum(1 for alert in alerts if alert.get("type") == kind) for kind in _ALERT_ENDPOINTS
    }
    counts["total"] = len(alerts)
    return {
        "source": "github",
        "available": not errors or bool(alerts),
        "owner": owner,
        "repo": repo,
        "alerts": alerts,
        "counts": counts,
        "errors": errors,
        "side_effects": [],
    }
