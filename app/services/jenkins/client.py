"""Jenkins REST API client."""

from __future__ import annotations

from typing import Any

import httpx


class JenkinsClient:
    """Small Jenkins REST API client."""

    def __init__(self, *, base_url: str, username: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth = (username, token)

    def _get(self, path: str) -> Any:
        response = httpx.get(
            f"{self.base_url}{path}",
            auth=self.auth,
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()

    def list_jobs(self) -> list[dict[str, Any]]:
        """List Jenkins jobs."""
        data = self._get("/api/json")
        jobs = data.get("jobs", [])

        return [
            {
                "name": job.get("name"),
                "url": job.get("url"),
                "color": job.get("color"),
            }
            for job in jobs
        ]

    def list_builds(self, job_name: str) -> list[dict[str, Any]]:
        """List recent builds for a Jenkins job."""
        data = self._get(f"/job/{job_name}/api/json")
        builds = data.get("builds", [])

        return [
            {
                "number": build.get("number"),
                "url": build.get("url"),
            }
            for build in builds
        ]
    def get_build_log(self, job_name: str, build_number: int) -> str:
        """Fetch Jenkins build console log."""

        response = httpx.get(
          f"{self.base_url}/job/{job_name}/{build_number}/consoleText",
        auth=self.auth,
        timeout=30.0,
    )

        response.raise_for_status()

        return response.text