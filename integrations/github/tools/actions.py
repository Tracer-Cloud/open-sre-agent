"""GitHub Actions workflow investigation tools - MCP-direct implementation."""

from __future__ import annotations

import json
from typing import Any, cast

from core.tool_framework.tool_decorator import tool
from core.tool_framework.utils.code_host_unavailable import code_host_unavailable_payload
from integrations.github.helpers import (
    github_creds,
    github_source_available,
    normalize_github_tool_result,
    resolve_github_mcp_config,
)
from integrations.github_mcp import call_github_mcp_tool


def _extract_json_text(result: dict[str, Any]) -> dict[str, Any] | str | None:
    text = str(result.get("text") or "").strip()
    if not text:
        return None

    try:
        parsed = json.loads(text)
        return cast(dict[str, Any], parsed)
    except json.JSONDecodeError:
        return text


def _extract_list(result: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Extract list of items from MCP tool result."""
    json_result = _extract_json_text(result)
    items: list[Any] = []
    if isinstance(json_result, dict):
        value = json_result.get(key)
        if isinstance(value, list):
            items = value
    return [item for item in items if isinstance(item, dict)]


def _extract_workflow_jobs(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract workflow jobs from MCP tool result."""
    json_result = _extract_json_text(result)
    if isinstance(json_result, dict) and "jobs" in json_result:
        jobs_raw = json_result["jobs"]
        if isinstance(jobs_raw, dict) and "jobs" in jobs_raw:
            target_list = jobs_raw["jobs"]
        else:
            target_list = jobs_raw
        if isinstance(target_list, list):
            return [_normalize_job(job) for job in target_list if isinstance(job, dict)]
    return []


def _extract_log_text(result: dict[str, Any]) -> str:
    """Extract log text from MCP tool result."""
    json_result = _extract_json_text(result)
    if isinstance(json_result, dict) and "logs_content" in json_result:
        return str(json_result["logs_content"] or "").strip()
    return str(result.get("text") or "").strip()


def _normalize_step(step: dict[str, Any]) -> dict[str, Any]:
    """Normalize step data."""
    return {
        "name": step.get("name", ""),
        "status": step.get("status", ""),
        "conclusion": step.get("conclusion", ""),
        "number": step.get("number"),
        "started_at": step.get("started_at", ""),
        "completed_at": step.get("completed_at", ""),
    }


def _normalize_job(job: dict[str, Any]) -> dict[str, Any]:
    """Normalize job data including steps."""
    steps_raw = job.get("steps")
    steps: list[dict[str, Any]] = []
    if isinstance(steps_raw, list):
        for item in steps_raw:
            if isinstance(item, dict):
                steps.append(_normalize_step(item))

    return {
        "id": job.get("id"),
        "run_id": job.get("run_id"),
        "name": job.get("name", ""),
        "status": job.get("status", ""),
        "conclusion": job.get("conclusion", ""),
        "started_at": job.get("started_at", ""),
        "completed_at": job.get("completed_at", ""),
        "runner_name": job.get("runner_name", ""),
        "runner_group_name": job.get("runner_group_name", ""),
        "labels": job.get("labels", []),
        "html_url": job.get("html_url", ""),
        "steps": steps,
    }


def _normalize_run(run: dict[str, Any]) -> dict[str, Any]:
    """Normalize workflow run data."""
    actor_raw = run.get("actor")
    actor = actor_raw if isinstance(actor_raw, dict) else {}
    triggering_actor_raw = run.get("triggering_actor")
    triggering_actor = triggering_actor_raw if isinstance(triggering_actor_raw, dict) else {}

    pull_requests_raw = run.get("pull_requests")
    pull_requests: list[dict[str, Any]] = []
    if isinstance(pull_requests_raw, list):
        for item in pull_requests_raw:
            if isinstance(item, dict):
                head_raw = item.get("head")
                head = head_raw if isinstance(head_raw, dict) else {}
                pull_requests.append(
                    {
                        "number": item.get("number"),
                        "url": item.get("html_url", ""),
                        "head_branch": head.get("ref", ""),
                    }
                )

    return {
        "id": run.get("id"),
        "name": run.get("name", ""),
        "display_title": run.get("display_title", ""),
        "head_branch": run.get("head_branch", ""),
        "head_sha": run.get("head_sha", ""),
        "event": run.get("event", ""),
        "status": run.get("status", ""),
        "conclusion": run.get("conclusion", ""),
        "run_number": run.get("run_number"),
        "run_attempt": run.get("run_attempt"),
        "html_url": run.get("html_url", ""),
        "created_at": run.get("created_at", ""),
        "updated_at": run.get("updated_at", ""),
        "actor": actor.get("login", "") if isinstance(actor, dict) else "",
        "triggering_actor": triggering_actor.get("login", "")
        if isinstance(triggering_actor, dict)
        else "",
        "pull_requests": pull_requests,
    }


UNGROUPED_SECTION_NAME = "ungrouped"


def _append_log_section(sections: list[dict[str, str]], name: str, lines: list[str]) -> None:
    """Append a non-empty log section preserving original ordering."""
    text = "\n".join(lines).strip()
    if text:
        sections.append({"name": name, "text": text})


def _extract_log_sections(log_text: str) -> list[dict[str, str]]:
    """Extract sections from GitHub Actions log output (marked by ##[group]/##[endgroup])."""
    sections: list[dict[str, str]] = []
    current_name: str | None = None
    current_lines: list[str] = []
    saw_group = False

    for line in log_text.splitlines():
        if line.startswith("##[group]"):
            saw_group = True
            _append_log_section(sections, current_name or UNGROUPED_SECTION_NAME, current_lines)
            current_name = line[len("##[group]") :].strip()
            current_lines = []
            continue
        if line.startswith("##[endgroup]"):
            _append_log_section(sections, current_name or UNGROUPED_SECTION_NAME, current_lines)
            current_name = None
            current_lines = []
            continue
        current_lines.append(line)

    if saw_group:
        _append_log_section(sections, current_name or UNGROUPED_SECTION_NAME, current_lines)

    if not saw_group:
        return [{"name": "full-log", "text": log_text.strip()}]
    return [section for section in sections if section.get("text")]


def extract_step_log(
    log_text: str,
    *,
    step_name: str = "",
    step_number: int | None = None,
) -> dict[str, Any]:
    """Extract the log text for a specific step in a GitHub Actions job log,
    using grouping markers if available."""
    sections = _extract_log_sections(log_text)
    selected: dict[str, str] | None = None
    match_strategy = "full-log"
    selected_idx = -1

    if step_name:
        needle = step_name.strip().lower()
        for i, section in enumerate(sections):
            if needle and needle in section.get("name", "").lower():
                selected = section
                match_strategy = "step_name"
                selected_idx = i
                break

    group_count = sum(1 for s in sections if s.get("name") != UNGROUPED_SECTION_NAME)
    if selected is None and step_number is not None and 1 <= step_number <= group_count:
        group_counter = 0
        for i, section in enumerate(sections):
            if section.get("name") != UNGROUPED_SECTION_NAME:
                group_counter += 1
            if group_counter == step_number:
                selected = section
                match_strategy = "step_number"
                selected_idx = i
                break

    if selected is None:
        selected = {"name": "full-log", "text": log_text.strip()}

    text = selected.get("text", "")

    # Merge trailing ungrouped annotations into the matched step log
    if selected_idx != -1 and selected_idx + 1 < len(sections):
        next_section = sections[selected_idx + 1]
        if next_section.get("name") == UNGROUPED_SECTION_NAME:
            text += "\n" + next_section.get("text", "")

    return {
        "step_name": selected.get("name", ""),
        "match_strategy": match_strategy,
        "log_text": text,
    }


def _github_actions_is_available(sources: dict[str, dict]) -> bool:
    """Check if GitHub Actions tool should be available."""
    github = sources.get("github", {})
    return bool(github_source_available(sources) and github.get("owner") and github.get("repo"))


def _github_actions_repo_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract repo parameters for GitHub Actions tools."""
    github = sources["github"]
    params: dict[str, Any] = {
        "owner": github["owner"],
        "repo": github["repo"],
        **github_creds(github),
    }
    return params


def _github_actions_run_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract run/job parameters for GitHub Actions tools."""
    github = sources["github"]
    params = _github_actions_repo_params(sources)
    if github.get("run_id") is not None:
        params["run_id"] = github["run_id"]
    if github.get("job_id") is not None:
        params["job_id"] = github["job_id"]
    return params


@tool(
    name="list_github_actions_workflow_runs",
    source="github",
    description="List recent GitHub Actions workflow runs for a repository.",
    use_cases=[
        "Checking which deploy or test workflow failed right before an incident",
        "Reviewing recent workflow status, trigger, and branch context",
        "Finding a run that matches an outage window or rollback event",
    ],
    requires=["owner", "repo"],
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "branch": {"type": "string", "default": ""},
            "status": {"type": "string", "default": ""},
            "event": {"type": "string", "default": ""},
            "per_page": {"type": "integer", "default": 30},
            "github_url": {"type": "string"},
            "github_mode": {"type": "string"},
            "github_token": {"type": "string"},
        },
        "required": ["owner", "repo"],
    },
    is_available=_github_actions_is_available,
    extract_params=_github_actions_repo_params,
)
def list_github_actions_workflow_runs(
    owner: str,
    repo: str,
    branch: str = "",
    status: str = "",
    event: str = "",
    per_page: int = 30,
    github_url: str | None = None,
    github_mode: str | None = None,
    github_token: str | None = None,
    github_command: str | None = None,
    github_args: list[str] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List recent GitHub Actions workflow runs for a repository."""
    config = resolve_github_mcp_config(
        github_url, github_mode, github_token, github_command, github_args
    )
    if config is None:
        return code_host_unavailable_payload(
            source="github",
            integration_name="GitHub Actions",
            empty_key="workflow_runs",
            empty_value=[],
        )

    workflow_runs_filter: dict[str, Any] = {}
    if branch:
        workflow_runs_filter["branch"] = branch
    if status:
        workflow_runs_filter["status"] = status
    if event:
        workflow_runs_filter["event"] = event

    arguments: dict[str, Any] = {
        "method": "list_workflow_runs",
        "owner": owner,
        "repo": repo,
        "per_page": per_page,
    }
    if workflow_runs_filter:
        arguments["workflow_runs_filter"] = workflow_runs_filter

    result = call_github_mcp_tool(config, "actions_list", arguments)
    payload = normalize_github_tool_result(result)
    if not isinstance(payload, dict):
        return {"error": "Unexpected payload format returned from GitHub MCP tool"}

    if payload.get("available"):
        workflow_runs_raw = _extract_list(result, "workflow_runs")
        workflow_runs = [_normalize_run(item) for item in workflow_runs_raw]
        payload["workflow_runs"] = workflow_runs
        payload["total"] = len(workflow_runs)
    else:
        payload["workflow_runs"] = []
        payload["total"] = 0

    payload["branch"] = branch
    payload["status"] = status
    payload["event"] = event

    return payload


@tool(
    name="list_github_actions_active_runs",
    source="github",
    description="List GitHub Actions workflow runs that are currently queued or in progress.",
    use_cases=[
        "Seeing what deployment jobs are still running during an incident",
        "Spotting queued deploys that may be waiting on a shared runner or lock",
    ],
    requires=["owner", "repo"],
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "per_page": {"type": "integer", "default": 30},
            "github_url": {"type": "string"},
            "github_mode": {"type": "string"},
            "github_token": {"type": "string"},
        },
        "required": ["owner", "repo"],
    },
    is_available=_github_actions_is_available,
    extract_params=_github_actions_repo_params,
)
def list_github_actions_active_runs(
    owner: str,
    repo: str,
    per_page: int = 30,
    github_url: str | None = None,
    github_mode: str | None = None,
    github_token: str | None = None,
    github_command: str | None = None,
    github_args: list[str] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List GitHub Actions workflow runs that are currently queued or in progress."""
    config = resolve_github_mcp_config(
        github_url, github_mode, github_token, github_command, github_args
    )
    if config is None:
        return code_host_unavailable_payload(
            source="github",
            integration_name="GitHub Actions",
            empty_key="workflow_runs",
            empty_value=[],
        )

    # Fetch queued runs
    queued_result = call_github_mcp_tool(
        config,
        "actions_list",
        {
            "method": "list_workflow_runs",
            "owner": owner,
            "repo": repo,
            "per_page": per_page,
            "workflow_runs_filter": {"status": "queued"},
        },
    )

    # Fetch in_progress runs
    in_progress_result = call_github_mcp_tool(
        config,
        "actions_list",
        {
            "method": "list_workflow_runs",
            "owner": owner,
            "repo": repo,
            "per_page": per_page,
            "workflow_runs_filter": {"status": "in_progress"},
        },
    )

    # Check for errors
    if queued_result.get("is_error") or in_progress_result.get("is_error"):
        error_texts: list[str] = []
        for result in (queued_result, in_progress_result):
            if result.get("is_error") and result.get("text"):
                error_texts.append(str(result.get("text")))

        error_msg = " | ".join(error_texts) if error_texts else "Failed to list active runs"

        return code_host_unavailable_payload(
            source="github",
            integration_name="GitHub Actions",
            empty_key="workflow_runs",
            empty_value=[],
        ) | {"error": error_msg}

    # Combine and deduplicate
    combined: list[dict[str, Any]] = []
    seen_ids: set[Any] = set()

    for result in (queued_result, in_progress_result):
        runs_raw = _extract_list(result, "workflow_runs")
        for run in runs_raw:
            run_id = run.get("id")
            if run_id is not None and run_id not in seen_ids:
                seen_ids.add(run_id)
                combined.append(_normalize_run(run))

    combined.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)

    return {
        "source": "github",
        "available": True,
        "workflow_runs": combined,
        "total": len(combined),
    }


@tool(
    name="list_github_actions_run_jobs",
    source="github",
    description="List jobs and step outcomes for a GitHub Actions workflow run.",
    use_cases=[
        "Finding which job failed in a deployment workflow",
        "Checking step-by-step status for test, build, and deploy jobs",
    ],
    requires=["owner", "repo", "run_id"],
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "run_id": {"type": "integer"},
            "github_url": {"type": "string"},
            "github_mode": {"type": "string"},
            "github_token": {"type": "string"},
        },
        "required": ["owner", "repo", "run_id"],
    },
    is_available=_github_actions_is_available,
    extract_params=_github_actions_run_params,
)
def list_github_actions_run_jobs(
    owner: str,
    repo: str,
    run_id: int,
    github_url: str | None = None,
    github_mode: str | None = None,
    github_token: str | None = None,
    github_command: str | None = None,
    github_args: list[str] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List jobs and step outcomes for a GitHub Actions workflow run."""
    config = resolve_github_mcp_config(
        github_url, github_mode, github_token, github_command, github_args
    )
    if config is None:
        return code_host_unavailable_payload(
            source="github",
            integration_name="GitHub Actions",
            empty_key="jobs",
            empty_value=[],
        )

    result = call_github_mcp_tool(
        config,
        "actions_list",
        {
            "method": "list_workflow_jobs",
            "owner": owner,
            "repo": repo,
            "resource_id": str(run_id),
        },
    )
    payload = normalize_github_tool_result(result)
    if not isinstance(payload, dict):
        return {"error": "Unexpected payload format returned from GitHub MCP tool"}

    if payload.get("available"):
        jobs = _extract_workflow_jobs(result)
        payload["jobs"] = jobs
        payload["total"] = len(jobs)
    else:
        payload["jobs"] = []
        payload["total"] = 0

    payload["workflow_run_id"] = run_id
    return payload


@tool(
    name="get_github_actions_step_log",
    source="github",
    description="Fetch the log output for a failed GitHub Actions job step.",
    use_cases=[
        "Reading the error output for the step that broke a deployment",
        "Checking the exact log snippet for a flaky test or secret-related failure",
    ],
    requires=["owner", "repo", "run_id", "job_id"],
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "run_id": {"type": "integer"},
            "job_id": {"type": "integer"},
            "step_name": {"type": "string", "default": ""},
            "step_number": {"type": "integer"},
            "tail_lines": {"type": "integer", "default": 500},
            "github_url": {"type": "string"},
            "github_mode": {"type": "string"},
            "github_token": {"type": "string"},
        },
        "required": ["owner", "repo", "run_id", "job_id"],
    },
    is_available=_github_actions_is_available,
    extract_params=_github_actions_run_params,
)
def get_github_actions_step_log(
    owner: str,
    repo: str,
    run_id: int,
    job_id: int,
    step_name: str = "",
    step_number: int | None = None,
    tail_lines: int = 500,
    github_url: str | None = None,
    github_mode: str | None = None,
    github_token: str | None = None,
    github_command: str | None = None,
    github_args: list[str] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Fetch the log output for a failed GitHub Actions job step."""
    config = resolve_github_mcp_config(
        github_url, github_mode, github_token, github_command, github_args
    )
    if config is None:
        return code_host_unavailable_payload(
            source="github",
            integration_name="GitHub Actions",
            empty_key="log_text",
            empty_value="",
        )

    # Fetch job metadata
    job_result = call_github_mcp_tool(
        config,
        "actions_get",
        {
            "method": "get_workflow_job",
            "owner": owner,
            "repo": repo,
            "resource_id": str(job_id),
        },
    )

    if job_result.get("is_error"):
        return code_host_unavailable_payload(
            source="github",
            integration_name="GitHub Actions",
            empty_key="log_text",
            empty_value="",
        ) | {"error": job_result.get("text")}

    job = _extract_json_text(job_result)
    if not isinstance(job, dict):
        return code_host_unavailable_payload(
            source="github",
            integration_name="GitHub Actions",
            empty_key="log_text",
            empty_value="",
        ) | {"error": "Unexpected job metadata format"}

    # Fetch job logs
    log_result = call_github_mcp_tool(
        config,
        "get_job_logs",
        {
            "owner": owner,
            "repo": repo,
            "job_id": job_id,
            "return_content": True,
            "tail_lines": tail_lines,
        },
    )

    if log_result.get("is_error"):
        return code_host_unavailable_payload(
            source="github",
            integration_name="GitHub Actions",
            empty_key="log_text",
            empty_value="",
        ) | {"error": log_result.get("text")}

    log_text = _extract_log_text(log_result)

    # Detect first failed step if not specified
    failed_step = ""
    steps_raw = job.get("steps") if isinstance(job, dict) else []
    normalized_steps = []
    if isinstance(steps_raw, list):
        normalized_steps = [_normalize_step(step) for step in steps_raw if isinstance(step, dict)]
        if not step_name:
            for step in steps_raw:
                if not isinstance(step, dict):
                    continue
                if step.get("conclusion") == "failure" or step.get("status") == "failure":
                    failed_step = str(step.get("name") or "")
                    break

    # Extract and filter step log
    extracted = extract_step_log(
        str(log_text),
        step_name=step_name or failed_step,
        step_number=step_number,
    )
    extracted.update(
        {
            "source": "github",
            "available": True,
            "workflow_run_id": run_id,
            "job_id": job_id,
            "job_name": job.get("name", ""),
            "job_conclusion": job.get("conclusion", ""),
            "job_steps": normalized_steps,
        }
    )
    return extracted


__all__ = [
    "extract_step_log",
    "get_github_actions_step_log",
    "list_github_actions_active_runs",
    "list_github_actions_run_jobs",
    "list_github_actions_workflow_runs",
]
