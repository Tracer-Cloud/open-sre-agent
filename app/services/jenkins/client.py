"""Jenkins REST API client.

Wraps the Jenkins endpoints used to correlate builds/deployments with incidents:
recent builds, build console logs, the job list, and currently running builds.
Credentials come from the user's Jenkins integration stored locally or via env vars.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from app.integrations.jenkins import JenkinsConfig
from app.services._error_helpers import capture_service_error

logger = logging.getLogger(__name__)

_MAX_JOB_NAME_LEN = 256
_MAX_LOG_CHARS = 50_000

# Jenkins encodes a job's last-build status in a "color" field (a legacy ball-color scheme).
_COLOR_STATUS = {
    "blue": "SUCCESS",
    "green": "SUCCESS",
    "red": "FAILURE",
    "yellow": "UNSTABLE",
    "aborted": "ABORTED",
    "grey": "NOT_BUILT",
    "disabled": "DISABLED",
    "notbuilt": "NOT_BUILT",
}


def _safe_job_name(raw: str) -> str | None:
    """Reject empty, oversized, or traversal-prone job names before building a URL path."""
    cleaned = (raw or "").strip()
    if not cleaned or len(cleaned) > _MAX_JOB_NAME_LEN:
        return None
    if ".." in cleaned or "/" in cleaned or "\\" in cleaned:
        return None
    return cleaned


def _iso_from_ms(value: object) -> str:
    """Convert a Jenkins epoch-millisecond timestamp to an ISO-8601 UTC string."""
    if not isinstance(value, (int, float, str)):
        return ""
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return ""
    if ms <= 0:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def _status_from_color(color: object) -> tuple[str, bool]:
    """Map a Jenkins job "color" to a (status, is_building) pair.

    A trailing "_anime" suffix means a build is currently in progress.
    """
    raw = str(color or "").strip().lower()
    building = raw.endswith("_anime")
    base = raw[: -len("_anime")] if building else raw
    return _COLOR_STATUS.get(base, base.upper() or "UNKNOWN"), building


def _shape_build(job_name: str, build: dict[str, Any]) -> dict[str, Any]:
    """Normalize one raw Jenkins build object into our stable output shape."""
    result = build.get("result")
    building = bool(build.get("building")) or result is None
    return {
        "job": job_name,
        "number": build.get("number"),
        # result is null while a build is still running; surface RUNNING explicitly.
        "status": "RUNNING" if building else str(result or "UNKNOWN"),
        "building": building,
        "timestamp": _iso_from_ms(build.get("timestamp")),
        "duration_ms": build.get("duration", 0),
        "url": build.get("url", ""),
    }


class JenkinsClient:
    """Synchronous client for the Jenkins REST API."""

    def __init__(self, config: JenkinsConfig) -> None:
        self.config = config
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.config.api_base_url,
                auth=self.config.auth,
                timeout=self.config.timeout_seconds,
            )
        return self._client

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> JenkinsClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def list_builds(
        self,
        job_name: str,
        limit: int = 10,
        status: str = "",
    ) -> dict[str, Any]:
        """List recent builds for a job, newest first.

        Args:
            job_name: The Jenkins job (project) name.
            limit: Maximum number of builds to return (capped at 50).
            status: Optional status filter, e.g. "FAILURE", "SUCCESS", "RUNNING".
        """
        safe_name = _safe_job_name(job_name)
        if not safe_name:
            return {"success": False, "error": "invalid job name"}
        tree = "builds[number,result,timestamp,duration,url,building]"
        try:
            resp = self._get_client().get(f"/job/{safe_name}/api/json", params={"tree": tree})
            resp.raise_for_status()
            data = resp.json()
            builds = [
                _shape_build(safe_name, b) for b in data.get("builds", []) if isinstance(b, dict)
            ]
            if status:
                wanted = status.strip().upper()
                builds = [b for b in builds if b["status"] == wanted]
            builds = builds[: max(1, min(limit, 50))]
            failed = [b for b in builds if b["status"] == "FAILURE"]
            return {
                "success": True,
                "job": safe_name,
                "builds": builds,
                "failed_builds": failed,
                "total": len(builds),
            }
        except httpx.HTTPStatusError as exc:
            return self._error("list_builds", exc, {"job": job_name, "status": status})
        except Exception as exc:
            return self._error("list_builds", exc, {"job": job_name, "status": status})

    def get_build_log(
        self,
        job_name: str,
        build_number: int,
        max_chars: int = _MAX_LOG_CHARS,
    ) -> dict[str, Any]:
        """Fetch the console log for a specific build, tail-truncated to ``max_chars``."""
        safe_name = _safe_job_name(job_name)
        if not safe_name:
            return {"success": False, "error": "invalid job name"}
        try:
            number = int(build_number)
        except (TypeError, ValueError):
            return {"success": False, "error": "invalid build number"}

        try:
            resp = self._get_client().get(f"/job/{safe_name}/{number}/consoleText")
            resp.raise_for_status()
            text = resp.text
            truncated = len(text) > max_chars
            # Keep the tail — failures and stack traces live at the end of a build log.
            log = text[-max_chars:] if truncated else text
            return {
                "success": True,
                "job": safe_name,
                "build_number": number,
                "log": log,
                "truncated": truncated,
            }
        except httpx.HTTPStatusError as exc:
            return self._error("get_build_log", exc, {"job": job_name, "build": build_number})
        except Exception as exc:
            return self._error("get_build_log", exc, {"job": job_name, "build": build_number})

    def list_jobs(self) -> dict[str, Any]:
        """List all jobs with their last-build status (decoded from the color field)."""
        tree = "jobs[name,url,color,lastBuild[number,result,timestamp,url]]"
        try:
            resp = self._get_client().get("/api/json", params={"tree": tree})
            resp.raise_for_status()
            data = resp.json()
            jobs = []
            for job in data.get("jobs", []):
                if not isinstance(job, dict):
                    continue
                status, building = _status_from_color(job.get("color"))
                last = job.get("lastBuild") if isinstance(job.get("lastBuild"), dict) else {}
                jobs.append(
                    {
                        "name": job.get("name", ""),
                        "url": job.get("url", ""),
                        "status": status,
                        "building": building,
                        "last_build_number": (last or {}).get("number"),
                        "last_build_at": _iso_from_ms((last or {}).get("timestamp")),
                    }
                )
            return {"success": True, "jobs": jobs, "total": len(jobs)}
        except httpx.HTTPStatusError as exc:
            return self._error("list_jobs", exc, {})
        except Exception as exc:
            return self._error("list_jobs", exc, {})

    def list_running_builds(self) -> dict[str, Any]:
        """List builds currently in progress across all jobs."""
        tree = "jobs[name,builds[number,building,result,timestamp,url]{0,5}]"
        try:
            resp = self._get_client().get("/api/json", params={"tree": tree})
            resp.raise_for_status()
            data = resp.json()
            running = []
            for job in data.get("jobs", []):
                if not isinstance(job, dict):
                    continue
                name = job.get("name", "")
                for build in job.get("builds", []):
                    if isinstance(build, dict) and build.get("building"):
                        running.append(_shape_build(name, build))
            return {"success": True, "running_builds": running, "total": len(running)}
        except httpx.HTTPStatusError as exc:
            return self._error("list_running_builds", exc, {})
        except Exception as exc:
            return self._error("list_running_builds", exc, {})

    def _error(
        self,
        method: str,
        exc: Exception,
        extras: dict[str, Any],
    ) -> dict[str, Any]:
        capture_service_error(
            exc, logger=logger, integration="jenkins", method=method, extras=extras
        )
        if isinstance(exc, httpx.HTTPStatusError):
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        return {"success": False, "error": str(exc)}


def make_jenkins_client(
    base_url: str | None,
    username: str | None = None,
    api_token: str | None = None,
) -> JenkinsClient | None:
    """Build a configured JenkinsClient, returning None if base URL or token is absent."""
    url = (base_url or "").strip()
    token = (api_token or "").strip()
    if not url or not token:
        return None
    try:
        return JenkinsClient(
            JenkinsConfig(base_url=url, username=(username or "").strip(), api_token=token)
        )
    except Exception:
        return None
