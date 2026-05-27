"""Jenkins CI/CD investigation tools."""

from __future__ import annotations

from typing import Any

from app.integrations.jenkins import get_jenkins_jobs
from app.services.jenkins.client import JenkinsClient
from app.tools.tool_decorator import tool
from app.services.jenkins.client import JenkinsClient


def _jenkins_available(sources: dict[str, dict]) -> bool:
    jenkins = sources.get("jenkins", {})
    return bool(jenkins.get("jenkins_url") and jenkins.get("jenkins_username") and jenkins.get("jenkins_token"))


def _jenkins_creds(jenkins: dict[str, Any]) -> dict[str, Any]:
    return {
        "jenkins_url": jenkins.get("jenkins_url"),
        "jenkins_username": jenkins.get("jenkins_username"),
        "jenkins_token": jenkins.get("jenkins_token"),
    }


def _list_jenkins_jobs_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    jenkins = sources["jenkins"]
    return {
        **_jenkins_creds(jenkins),
    }


@tool(
    name="list_jenkins_jobs",
    source="jenkins",
    description="List Jenkins jobs and basic job metadata.",
    use_cases=[
        "Checking whether a Jenkins job recently ran near an incident",
        "Finding CI/CD jobs related to a service during an investigation",
        "Identifying Jenkins jobs before fetching builds or logs",
    ],
    requires=["jenkins_url", "jenkins_username", "jenkins_token"],
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "jenkins_url": {"type": "string"},
            "jenkins_username": {"type": "string"},
            "jenkins_token": {"type": "string"},
        },
        "required": ["jenkins_url", "jenkins_username", "jenkins_token"],
    },
    is_available=_jenkins_available,
    extract_params=_list_jenkins_jobs_extract_params,
)
def list_jenkins_jobs(
    jenkins_url: str,
    jenkins_username: str,
    jenkins_token: str,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List Jenkins jobs."""
    result = get_jenkins_jobs(
        jenkins_url=jenkins_url,
        username=jenkins_username,
        token=jenkins_token,
    )
    return {"source": "jenkins", "available": True, "jobs": result}
@tool(
    name="list_jenkins_builds",
    source="jenkins",
    description="List recent builds for a Jenkins job.",
    use_cases=[
        "Checking failed Jenkins builds during an incident",
        "Correlating deployments with alerts",
    ],
    requires=["job_name"],
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "job_name": {"type": "string"},
            "jenkins_url": {"type": "string"},
            "jenkins_username": {"type": "string"},
            "jenkins_token": {"type": "string"},
        },
        "required": [
            "job_name",
            "jenkins_url",
            "jenkins_username",
            "jenkins_token",
        ],
    },
)
def list_jenkins_builds(
    job_name: str,
    jenkins_url: str,
    jenkins_username: str,
    jenkins_token: str,
    **_kwargs: Any,
) -> dict[str, Any]:
    client = JenkinsClient(
        base_url=jenkins_url,
        username=jenkins_username,
        token=jenkins_token,
    )

    builds = client.list_builds(job_name)

    return {
        "source": "jenkins",
        "available": True,
        "builds": builds,
    }
@tool(
    name="get_jenkins_build_log",
    source="jenkins",
    description="Fetch Jenkins build console logs.",
    use_cases=[
        "Inspecting CI/CD deployment failures",
        "Reviewing Jenkins console output during incidents",
    ],
    requires=["job_name", "build_number"],
    surfaces=("investigation", "chat"),
    input_schema={
        "type": "object",
        "properties": {
            "job_name": {"type": "string"},
            "build_number": {"type": "integer"},
            "jenkins_url": {"type": "string"},
            "jenkins_username": {"type": "string"},
            "jenkins_token": {"type": "string"},
        },
        "required": [
            "job_name",
            "build_number",
            "jenkins_url",
            "jenkins_username",
            "jenkins_token",
        ],
    },
)
def get_jenkins_build_log(
    job_name: str,
    build_number: int,
    jenkins_url: str,
    jenkins_username: str,
    jenkins_token: str,
    **_kwargs: Any,
) -> dict[str, Any]:
    client = JenkinsClient(
        base_url=jenkins_url,
        username=jenkins_username,
        token=jenkins_token,
    )

    log = client.get_build_log(job_name, build_number)

    return {
        "source": "jenkins",
        "available": True,
        "log": log,
    }