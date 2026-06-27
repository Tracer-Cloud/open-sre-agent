"""Synthetic RCA scenario using Jenkins as a supplementary evidence source.

Models the issue's core use case — "was there a recent build or deployment
that coincides with this alert?" — end-to-end through the tool layer, with a
fixture client (no live Jenkins). Validates that a failed deploy build and its
error log surface as investigation evidence.
"""

from __future__ import annotations

from typing import Any

import pytest

import tools.jenkins_tools as JenkinsTool


class _FixtureJenkinsClient:
    """Context-managed fixture client returning realistic Jenkins responses."""

    def __enter__(self) -> _FixtureJenkinsClient:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def list_builds(self, job_name: str, limit: int = 10, status: str = "") -> dict[str, Any]:
        builds = [
            {
                "job": job_name,
                "number": 42,
                "status": "FAILURE",
                "building": False,
                "timestamp": "2026-05-30T14:07:12+00:00",
                "duration_ms": 11000,
                "url": f"http://jenkins.local/job/{job_name}/42/",
            },
            {
                "job": job_name,
                "number": 41,
                "status": "SUCCESS",
                "building": False,
                "timestamp": "2026-05-30T13:50:00+00:00",
                "duration_ms": 12000,
                "url": f"http://jenkins.local/job/{job_name}/41/",
            },
        ]
        if status:
            builds = [b for b in builds if b["status"] == status.upper()]
        builds = builds[: max(1, limit)]
        failed = [b for b in builds if b["status"] == "FAILURE"]
        return {
            "success": True,
            "job": job_name,
            "builds": builds,
            "failed_builds": failed,
            "total": len(builds),
        }

    def get_build_log(self, job_name: str, build_number: int, **_: Any) -> dict[str, Any]:
        return {
            "success": True,
            "job": job_name,
            "build_number": build_number,
            "log": (
                "+ echo deploying release v1.4.2...\n"
                "+ echo running db migration\n"
                "ERROR: migration 0042_add_index failed: relation already exists\n"
                "Build step 'Execute shell' marked build as failure\n"
                "Finished: FAILURE\n"
            ),
            "truncated": False,
        }


@pytest.fixture
def fixture_client(monkeypatch: pytest.MonkeyPatch) -> _FixtureJenkinsClient:
    client = _FixtureJenkinsClient()
    monkeypatch.setattr(JenkinsTool, "_resolve_client", lambda *_a, **_k: client)
    return client


def test_jenkins_alert_source_maps_to_tools() -> None:
    """A jenkins-sourced alert auto-seeds and prioritizes the jenkins tools."""
    from core.domain.alerts.alert_source import (
        ALERT_SOURCE_TO_SEED_TOOL_SOURCES as seed_map,
    )
    from core.domain.alerts.alert_source import (
        ALERT_SOURCE_TO_TOOL_SOURCES as priority_map,
    )

    assert seed_map.get("jenkins") == ("jenkins",)
    assert priority_map.get("jenkins") == ("jenkins",)


def test_jenkins_tools_are_registered() -> None:
    """The four Jenkins tools are discoverable for investigations and chat."""
    from tools.registry import get_registered_tools

    names = {t.name for t in get_registered_tools() if t.source == "jenkins"}
    assert names == {
        "list_jenkins_builds",
        "get_jenkins_build_log",
        "get_jenkins_pipeline_stages",
        "list_jenkins_jobs",
        "list_jenkins_running_builds",
    }


def test_failed_deploy_surfaces_as_evidence(fixture_client: _FixtureJenkinsClient) -> None:
    """Recent builds for the alerting job surface the failed deploy near alert time."""
    result = JenkinsTool.list_jenkins_builds(
        "payment-service-deploy", jenkins_url="http://jenkins.local", jenkins_token="t"
    )
    assert result["available"] is True
    assert result["source"] == "jenkins"
    assert result["total"] == 2
    assert len(result["failed_builds"]) == 1
    assert result["failed_builds"][0]["number"] == 42


def test_build_log_reveals_root_cause(fixture_client: _FixtureJenkinsClient) -> None:
    """The failed build's console log exposes the failing migration — the RCA signal."""
    result = JenkinsTool.get_jenkins_build_log(
        "payment-service-deploy", 42, jenkins_url="http://jenkins.local", jenkins_token="t"
    )
    assert result["available"] is True
    assert "migration 0042_add_index failed" in result["log"]
    assert "Finished: FAILURE" in result["log"]
