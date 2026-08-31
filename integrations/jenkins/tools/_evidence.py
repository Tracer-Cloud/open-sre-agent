"""Evidence mappers for the Jenkins CI/CD investigation tools."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry

_FAILED_BUILD_STATUSES = frozenset({"FAILURE"})
_FAILED_JOB_STATUSES = frozenset({"FAILURE"})
_FAILED_STAGE_STATUSES = frozenset({"FAILED"})


def map_list_jenkins_builds(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite the build count and how many failed, qualifying page-capped totals.

    ``total`` mirrors ``len(builds)`` after the client's own limit slice, so a
    returned count at that ceiling may understate the true number of builds
    -- use the "N+" convention against the caller's requested limit. When a
    ``status`` filter is set, every returned build already matches it, so
    cite the filter instead of a derived failed count (a filtered "0 failed"
    would misrepresent unfiltered reality).
    """
    if not output.get("available"):
        return
    builds = output.get("builds") or []
    if not builds:
        return
    total = output.get("total", len(builds))
    requested_limit = tool_input.get("limit", 10)
    truncated = total >= max(requested_limit, 1)
    total_label = f"{total}+" if truncated else str(total)
    status_filter = tool_input.get("status")
    if status_filter:
        summary = f"{total_label} build(s) with status '{status_filter}'"
    else:
        failed = sum(
            1 for b in builds if str(b.get("status", "")).upper() in _FAILED_BUILD_STATUSES
        )
        failed_label = f"{failed}+" if truncated else str(failed)
        summary = f"{total_label} build(s), {failed_label} failed"
    job = output.get("job")
    if job:
        summary += f" for '{job}'"
    record_evidence_entry(
        evidence,
        source="list_jenkins_builds",
        label="Jenkins Builds",
        summary=summary,
    )


def map_get_jenkins_build_log(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the console log length, using the client's own tail-truncation flag."""
    if not output.get("available"):
        return
    log = output.get("log") or ""
    if not log:
        return
    parts = [f"{len(log)} char(s) of console log"]
    if output.get("truncated"):
        parts.append("truncated to tail")
    job = output.get("job")
    build_number = output.get("build_number")
    if job and build_number is not None:
        parts.append(f"for '{job}' #{build_number}")
    record_evidence_entry(
        evidence,
        source="get_jenkins_build_log",
        label="Jenkins Build Log",
        summary=", ".join(parts),
    )


def map_get_jenkins_pipeline_stages(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the stage count and how many failed.

    Freestyle jobs (``is_pipeline`` False) have no stages -- that's an
    expected, unremarkable outcome, not a finding worth citing.
    """
    if not output.get("available") or not output.get("is_pipeline"):
        return
    stages = output.get("stages") or []
    if not stages:
        return
    failed = sum(1 for s in stages if str(s.get("status", "")).upper() in _FAILED_STAGE_STATUSES)
    summary = f"{len(stages)} stage(s), {failed} failed"
    status = output.get("status")
    if status:
        summary += f" (build status: {status})"
    job = output.get("job")
    build_number = output.get("build_number")
    if job and build_number is not None:
        summary += f" for '{job}' #{build_number}"
    record_evidence_entry(
        evidence,
        source="get_jenkins_pipeline_stages",
        label="Jenkins Pipeline Stages",
        summary=summary,
    )


def map_list_jenkins_jobs(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the job count and how many are currently failing.

    ``truncated`` is the client's own explicit signal (folder-depth or job
    cap dropped jobs), not an inferred heuristic.
    """
    if not output.get("available"):
        return
    jobs = output.get("jobs") or []
    if not jobs:
        return
    total = output.get("total", len(jobs))
    truncated = output.get("truncated", False)
    total_label = f"{total}+" if truncated else str(total)
    failing = sum(1 for j in jobs if str(j.get("status", "")).upper() in _FAILED_JOB_STATUSES)
    failing_label = f"{failing}+" if truncated else str(failing)
    record_evidence_entry(
        evidence,
        source="list_jenkins_jobs",
        label="Jenkins Jobs",
        summary=f"{total_label} job(s), {failing_label} failing",
    )


def map_list_jenkins_running_builds(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite how many builds are currently running.

    ``truncated`` is the client's own explicit signal (job cap dropped some
    jobs from the scan), not an inferred heuristic.
    """
    if not output.get("available"):
        return
    running = output.get("running_builds") or []
    if not running:
        return
    total = output.get("total", len(running))
    truncated = output.get("truncated", False)
    total_label = f"{total}+" if truncated else str(total)
    record_evidence_entry(
        evidence,
        source="list_jenkins_running_builds",
        label="Jenkins Running Builds",
        summary=f"{total_label} build(s) currently running",
    )
