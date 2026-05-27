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
        """List recent builds for a Jenkins job with status and timestamp."""
        data = self._get(f"/job/{job_name}/api/json")
        builds = data.get("builds", [])

        results = []

        for build in builds:
            build_number = build.get("number")
            if build_number is None:
                continue

            build_data = self._get(f"/job/{job_name}/{build_number}/api/json")

            results.append(
                {
                    "number": build_data.get("number"),
                    "url": build_data.get("url"),
                    "status": build_data.get("result"),
                    "timestamp": build_data.get("timestamp"),
                    "building": build_data.get("building"),
                }
            )

        return results

    def get_build_log(self, job_name: str, build_number: int) -> str:
      """Fetch Jenkins build console log."""
      response = httpx.get(
            f"{self.base_url}/job/{job_name}/{build_number}/consoleText",
        auth=self.auth,
        timeout=30.0,
    )
      response.raise_for_status()
      return str(response.text)

    def list_running_builds(self) -> list[dict[str, Any]]:
        """List currently running Jenkins builds."""
        data = self._get("/api/json")
        jobs = data.get("jobs", [])

        running = []

        for job in jobs:
            if "anime" in str(job.get("color", "")):
                running.append(
                    {
                        "name": job.get("name"),
                        "url": job.get("url"),
                        "color": job.get("color"),
                    }
                )

        return running
    def list_pipeline_stages(self, job_name: str, build_number: int) -> list[dict[str, Any]]:
       """List Jenkins pipeline stages for a build."""
       data = self._get(f"/job/{job_name}/{build_number}/wfapi/describe")
       stages = data.get("stages", [])

       return [
        {
            "name": stage.get("name"),
            "status": stage.get("status"),
            "startTimeMillis": stage.get("startTimeMillis"),
            "durationMillis": stage.get("durationMillis"),
        }
        for stage in stages
    ]
