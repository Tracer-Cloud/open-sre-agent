"""Jenkins CI/CD investigation tools.

Surfaces recent builds, build logs, the job list, and running builds so the
investigation pipeline can answer: "did a recent build or deployment coincide
with this alert?"
"""

from __future__ import annotations

from typing import Any

from app.integrations.jenkins import jenkins_config_from_env
from app.services.jenkins import make_jenkins_client
from app.tools.tool_decorator import tool


def _jenkins_available(sources: dict) -> bool:
    return bool(sources.get("jenkins", {}).get("connection_verified"))


def _jenkins_creds(jk: dict) -> dict[str, Any]:
    # The resolved source dict stores connection fields as base_url/username/api_token
    # (from JenkinsConfig.model_dump); map them to the tool's param names.
    return {
        "jenkins_url": jk.get("base_url"),
        "jenkins_user": jk.get("username"),
        "jenkins_token": jk.get("api_token"),
    }


def _resolve_client(
    jenkins_url: str | None,
    jenkins_user: str | None,
    jenkins_token: str | None,
):
    """Build a client from explicit args, falling back to env-var config."""
    if any([jenkins_url, jenkins_token]):
        env = jenkins_config_from_env()
        return make_jenkins_client(
            jenkins_url or (env.base_url if env else ""),
            jenkins_user or (env.username if env else ""),
            jenkins_token or (env.api_token if env else ""),
        )
    env = jenkins_config_from_env()
    if env is None:
        return None
    return make_jenkins_client(env.base_url, env.username, env.api_token)


def _not_configured(payload_key: str) -> dict[str, Any]:
    return {
        "source": "jenkins",
        "available": False,
        "error": "jenkins integration is not configured.",
        payload_key: [],
    }


# ---------------------------------------------------------------------------
# list_jenkins_builds
# ---------------------------------------------------------------------------


def _list_jenkins_builds_available(sources: dict[str, dict]) -> bool:
    jk = sources.get("jenkins", {})
    return bool(_jenkins_available(sources) and jk.get("job_name"))


def _list_jenkins_builds_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    jk = sources["jenkins"]
    return {
        "job_name": jk["job_name"],
        "limit": 10,
        "status": jk.get("status", ""),
        **_jenkins_creds(jk),
    }


@tool(
    name="list_jenkins_builds",
    source="jenkins",
    description="List recent Jenkins builds for a job with status and timestamp.",
    use_cases=[
        "Checking whether a recent build or deployment coincided with the alert",
        "Identifying which build failed and when",
        "Correlating a deployment window with downstream errors in logs or metrics",
    ],
    requires=["job_name"],
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "job_name": {"type": "string"},
            "limit": {"type": "integer", "default": 10},
            "status": {
                "type": "string",
                "default": "",
                "description": "Optional filter: SUCCESS, FAILURE, RUNNING, ABORTED",
            },
            "jenkins_url": {"type": "string"},
            "jenkins_user": {"type": "string"},
            "jenkins_token": {"type": "string"},
        },
        "required": ["job_name"],
    },
    outputs={
        "builds": "Recent builds with status, timestamp, duration, and url",
        "failed_builds": "Subset of builds in FAILURE state",
    },
    is_available=_list_jenkins_builds_available,
    extract_params=_list_jenkins_builds_extract_params,
)
def list_jenkins_builds(
    job_name: str,
    limit: int = 10,
    status: str = "",
    jenkins_url: str | None = None,
    jenkins_user: str | None = None,
    jenkins_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List recent builds for a Jenkins job."""
    client = _resolve_client(jenkins_url, jenkins_user, jenkins_token)
    if client is None:
        return _not_configured("builds")
    with client:
        result = client.list_builds(job_name, limit=limit, status=status)
    if not result.get("success"):
        return {
            "source": "jenkins",
            "available": False,
            "error": result.get("error", "unknown error"),
            "builds": [],
        }
    return {
        "source": "jenkins",
        "available": True,
        "job": result.get("job", job_name),
        "builds": result.get("builds", []),
        "failed_builds": result.get("failed_builds", []),
        "total": result.get("total", 0),
    }


# ---------------------------------------------------------------------------
# get_jenkins_build_log
# ---------------------------------------------------------------------------


def _get_jenkins_build_log_available(sources: dict[str, dict]) -> bool:
    jk = sources.get("jenkins", {})
    return bool(_jenkins_available(sources) and jk.get("job_name"))


def _get_jenkins_build_log_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    jk = sources["jenkins"]
    return {
        "job_name": jk["job_name"],
        "build_number": jk.get("build_number", 0),
        **_jenkins_creds(jk),
    }


@tool(
    name="get_jenkins_build_log",
    source="jenkins",
    description="Fetch the console log for a specific Jenkins build.",
    use_cases=[
        "Reading the error output of a failed build",
        "Finding the stack trace or failing step that broke a deployment",
    ],
    requires=["job_name", "build_number"],
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "job_name": {"type": "string"},
            "build_number": {"type": "integer"},
            "jenkins_url": {"type": "string"},
            "jenkins_user": {"type": "string"},
            "jenkins_token": {"type": "string"},
        },
        "required": ["job_name", "build_number"],
    },
    outputs={
        "log": "Console log text (tail-truncated for large logs)",
        "truncated": "Whether the log was truncated",
    },
    is_available=_get_jenkins_build_log_available,
    extract_params=_get_jenkins_build_log_extract_params,
)
def get_jenkins_build_log(
    job_name: str,
    build_number: int,
    jenkins_url: str | None = None,
    jenkins_user: str | None = None,
    jenkins_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Fetch the console log for a specific Jenkins build."""
    client = _resolve_client(jenkins_url, jenkins_user, jenkins_token)
    if client is None:
        return {
            "source": "jenkins",
            "available": False,
            "error": "jenkins integration is not configured.",
            "log": "",
        }
    with client:
        result = client.get_build_log(job_name, build_number)
    if not result.get("success"):
        return {
            "source": "jenkins",
            "available": False,
            "error": result.get("error", "unknown error"),
            "log": "",
        }
    return {
        "source": "jenkins",
        "available": True,
        "job": result.get("job", job_name),
        "build_number": result.get("build_number", build_number),
        "log": result.get("log", ""),
        "truncated": result.get("truncated", False),
    }


# ---------------------------------------------------------------------------
# list_jenkins_jobs
# ---------------------------------------------------------------------------


def _list_jenkins_jobs_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    jk = sources.get("jenkins", {})
    return {**_jenkins_creds(jk)}


@tool(
    name="list_jenkins_jobs",
    source="jenkins",
    description="List Jenkins jobs with their last-build status.",
    use_cases=[
        "Discovering which jobs exist when the failing job name is unknown",
        "Getting an overview of which pipelines are passing or failing",
    ],
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "jenkins_url": {"type": "string"},
            "jenkins_user": {"type": "string"},
            "jenkins_token": {"type": "string"},
        },
    },
    outputs={"jobs": "Jobs with name, url, status, and last-build info"},
    is_available=_jenkins_available,
    extract_params=_list_jenkins_jobs_extract_params,
)
def list_jenkins_jobs(
    jenkins_url: str | None = None,
    jenkins_user: str | None = None,
    jenkins_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List Jenkins jobs with last-build status."""
    client = _resolve_client(jenkins_url, jenkins_user, jenkins_token)
    if client is None:
        return _not_configured("jobs")
    with client:
        result = client.list_jobs()
    if not result.get("success"):
        return {
            "source": "jenkins",
            "available": False,
            "error": result.get("error", "unknown error"),
            "jobs": [],
        }
    return {
        "source": "jenkins",
        "available": True,
        "jobs": result.get("jobs", []),
        "total": result.get("total", 0),
    }


# ---------------------------------------------------------------------------
# list_jenkins_running_builds
# ---------------------------------------------------------------------------


def _list_jenkins_running_builds_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    jk = sources.get("jenkins", {})
    return {**_jenkins_creds(jk)}


@tool(
    name="list_jenkins_running_builds",
    source="jenkins",
    description="List Jenkins builds currently in progress across all jobs.",
    use_cases=[
        "Checking whether a build is running right now during an active incident",
        "Spotting a long-running or stuck build that may be causing impact",
    ],
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "jenkins_url": {"type": "string"},
            "jenkins_user": {"type": "string"},
            "jenkins_token": {"type": "string"},
        },
    },
    outputs={"running_builds": "Builds currently in progress with job, number, and url"},
    is_available=_jenkins_available,
    extract_params=_list_jenkins_running_builds_extract_params,
)
def list_jenkins_running_builds(
    jenkins_url: str | None = None,
    jenkins_user: str | None = None,
    jenkins_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List currently running Jenkins builds."""
    client = _resolve_client(jenkins_url, jenkins_user, jenkins_token)
    if client is None:
        return _not_configured("running_builds")
    with client:
        result = client.list_running_builds()
    if not result.get("success"):
        return {
            "source": "jenkins",
            "available": False,
            "error": result.get("error", "unknown error"),
            "running_builds": [],
        }
    return {
        "source": "jenkins",
        "available": True,
        "running_builds": result.get("running_builds", []),
        "total": result.get("total", 0),
    }
