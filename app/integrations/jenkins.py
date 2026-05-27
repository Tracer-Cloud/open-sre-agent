"""Jenkins integration helpers."""

from __future__ import annotations

from typing import Any

from app.services.jenkins.client import JenkinsClient


def get_jenkins_jobs(
    *,
    jenkins_url: str,
    username: str,
    token: str,
) -> list[dict[str, Any]]:
    """List Jenkins jobs."""
    client = JenkinsClient(
        base_url=jenkins_url,
        username=username,
        token=token,
    )
    return client.list_jobs()
