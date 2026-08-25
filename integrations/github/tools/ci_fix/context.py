"""Resolve GitHub CI failures into a coding task."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Final

from infrastructure.safety.masking import MaskingPolicy, MaskingRules
from integrations.github.repo_scope import detect_git_remote_repo_scope
from integrations.github.tools.ci_fix.errors import (
    ERR_INVALID_INPUT,
    ERR_NO_FAILING_CHECKS,
    ERR_UNSUPPORTED_PR_BRANCH,
    GitHubCiFixError,
)
from integrations.github.tools.ci_fix.gh import run_gh_json, run_gh_text

_PR_URL_RE = re.compile(
    r"https?://github\.com/"
    r"(?P<owner>[^/\s]+)/(?P<repo>[^/\s#?]+)/pull/(?P<number>\d+)",
    re.IGNORECASE,
)
_ACTIONS_URL_RE = re.compile(
    r"/actions/runs/(?P<run_id>\d+)(?:/job/(?P<job_id>\d+))?",
    re.IGNORECASE,
)
CI_TARGET_BRANCH: Final = "branch"
CI_TARGET_PR: Final = "pr"
# CANCELLED is omitted: cancelled siblings of a real failure are noise, not a
# second root cause for the coding agent to chase.
_FAILED_CONCLUSIONS = frozenset({"ACTION_REQUIRED", "FAILURE", "STARTUP_FAILURE", "TIMED_OUT"})
_FAILED_STATES = frozenset({"ERROR", "FAILURE", "FAILED"})
_BRANCH_RUN_FIELDS = ",".join(
    [
        "conclusion",
        "createdAt",
        "databaseId",
        "displayTitle",
        "headBranch",
        "headSha",
        "name",
        "status",
        "url",
        "workflowName",
    ]
)
_PR_FIELDS = ",".join(
    [
        "number",
        "title",
        "url",
        "headRefName",
        "headRepositoryOwner",
        "headRepository",
        "headRefOid",
        "baseRefName",
        "isCrossRepository",
        "state",
        "statusCheckRollup",
    ]
)
_MAX_LOG_CHARS = 7000
_MAX_TASK_LOG_CHARS = 18000
_BRANCH_RUN_LIMIT = "20"


@dataclass(frozen=True)
class PullRequestRef:
    """Repository and PR identity parsed from a GitHub PR URL."""

    owner: str
    repo: str
    number: int


@dataclass(frozen=True)
class FailingCheck:
    """A failing PR check with optional GitHub Actions log context."""

    name: str
    conclusion: str
    details_url: str
    workflow_name: str
    run_id: str = ""
    job_id: str = ""
    log_excerpt: str = ""


@dataclass(frozen=True)
class CiFixContext:
    """Resolved GitHub CI failure and coding-agent task."""

    owner: str
    repo: str
    number: int | None
    title: str
    url: str
    base_branch: str
    head_branch: str
    head_sha: str
    skipped_check_names: tuple[str, ...]
    failing_checks: tuple[FailingCheck, ...]
    task: str
    target_kind: str = CI_TARGET_PR
    target_branch: str = ""


def parse_pr_url(pr_url: str | None) -> PullRequestRef | None:
    """Parse a GitHub pull request URL."""
    if not pr_url:
        return None
    match = _PR_URL_RE.search(pr_url.strip())
    if match is None:
        return None
    return PullRequestRef(
        owner=match.group("owner"),
        repo=match.group("repo").removesuffix(".git"),
        number=int(match.group("number")),
    )


def gather_ci_fix_context(
    *,
    owner: str | None = None,
    repo: str | None = None,
    pr_number: int | None = None,
    pr_url: str | None = None,
    branch: str | None = None,
    workspace: str | None = None,
    github_token: str | None = None,
) -> CiFixContext:
    """Resolve PR or branch metadata, failing checks, and log snippets."""
    parsed_url = parse_pr_url(pr_url)
    repo_owner = (parsed_url.owner if parsed_url else owner or "").strip()
    repo_name = (parsed_url.repo if parsed_url else repo or "").strip().removesuffix(".git")
    number = parsed_url.number if parsed_url else pr_number
    branch_name = _normalize_branch(branch)

    if not repo_owner or not repo_name:
        detected = detect_git_remote_repo_scope(workspace)
        if detected is not None:
            repo_owner, repo_name = detected
    if not repo_owner or not repo_name:
        raise GitHubCiFixError(
            ERR_INVALID_INPUT,
            "owner/repo is required unless pr_url or the workspace origin identifies a GitHub repo; no push was made.",
        )

    repo_full_name = f"{repo_owner}/{repo_name}"
    if number is None and branch_name:
        return _gather_branch_ci_fix_context(
            owner=repo_owner,
            repo=repo_name,
            branch=branch_name,
            github_token=github_token,
        )

    pr_selector = str(number) if number is not None else ""
    args = ["pr", "view"]
    if pr_selector:
        args.append(pr_selector)
    args.extend(["--json", _PR_FIELDS])
    pr = run_gh_json(args, repo=repo_full_name, github_token=github_token)

    resolved_number = _int_value(pr.get("number"))
    if resolved_number is None:
        raise GitHubCiFixError(
            ERR_INVALID_INPUT,
            "GitHub PR metadata did not include a PR number; no push was made.",
        )

    head_repo = _head_repo_full_name(pr)
    is_cross_repo = bool(pr.get("isCrossRepository"))
    if is_cross_repo or head_repo.lower() != repo_full_name.lower():
        head_branch = str(pr.get("headRefName") or "").strip()
        target = f"{head_repo}:{head_branch}" if head_repo else head_branch
        raise GitHubCiFixError(
            ERR_UNSUPPORTED_PR_BRANCH,
            (
                f"{repo_full_name}#{resolved_number} uses branch {target or '(unknown)'}; "
                "OpenSRE only pushes CI fixes to branches in the same repository, so no push was made."
            ),
        )

    rollup = _list_value(pr.get("statusCheckRollup"))
    checks = tuple(
        _failing_check_from_rollup(repo_full_name, item, github_token=github_token)
        for item in rollup
        if _is_failing_check(item)
    )
    if not checks:
        raise GitHubCiFixError(
            ERR_NO_FAILING_CHECKS,
            f"No failing CI checks found on {repo_full_name}#{resolved_number}; no push was made.",
        )

    title = str(pr.get("title") or "").strip()
    url = str(pr.get("url") or f"https://github.com/{repo_full_name}/pull/{resolved_number}")
    head_branch = str(pr.get("headRefName") or "").strip()
    ctx = CiFixContext(
        owner=repo_owner,
        repo=repo_name,
        number=resolved_number,
        title=title,
        url=url,
        base_branch=str(pr.get("baseRefName") or "").strip(),
        head_branch=head_branch,
        head_sha=str(pr.get("headRefOid") or "").strip(),
        skipped_check_names=tuple(_check_name(item) for item in rollup if _is_skipped(item)),
        failing_checks=checks,
        task="",
        target_branch=head_branch,
    )
    return replace(ctx, task=_build_task(ctx))


def _gather_branch_ci_fix_context(
    *,
    owner: str,
    repo: str,
    branch: str,
    github_token: str | None,
) -> CiFixContext:
    repo_full_name = f"{owner}/{repo}"
    payload = run_gh_json(
        [
            "run",
            "list",
            "--branch",
            branch,
            "--limit",
            _BRANCH_RUN_LIMIT,
            "--json",
            _BRANCH_RUN_FIELDS,
            "--jq",
            '{"runs": .}',
        ],
        repo=repo_full_name,
        github_token=github_token,
    )
    current_runs = _latest_branch_run_group(payload.get("runs"))
    failing_runs = _failing_runs(current_runs)
    if not failing_runs:
        raise GitHubCiFixError(
            ERR_NO_FAILING_CHECKS,
            f"No failing CI runs found on {repo_full_name} branch {branch}; no push was made.",
        )

    checks: list[FailingCheck] = []
    skipped_names: list[str] = []
    for failing_run in failing_runs:
        run_id = str(failing_run.get("databaseId") or "").strip()
        workflow_name = str(
            failing_run.get("workflowName") or failing_run.get("name") or "GitHub Actions"
        ).strip()
        run_url = str(
            failing_run.get("url")
            or (f"https://github.com/{repo_full_name}/actions/runs/{run_id}" if run_id else "")
        )
        run_checks, run_skipped = _checks_from_branch_run(
            repo_full_name,
            failing_run=failing_run,
            run_id=run_id,
            run_url=run_url,
            workflow_name=workflow_name,
            github_token=github_token,
        )
        checks.extend(run_checks)
        skipped_names.extend(run_skipped)

    primary = failing_runs[0]
    primary_id = str(primary.get("databaseId") or "").strip()
    primary_workflow = str(
        primary.get("workflowName") or primary.get("name") or "GitHub Actions"
    ).strip()
    url = str(
        primary.get("url")
        or (f"https://github.com/{repo_full_name}/actions/runs/{primary_id}" if primary_id else "")
    )
    if len(failing_runs) == 1:
        title = str(primary.get("displayTitle") or primary_workflow or f"CI on {branch}").strip()
    else:
        workflow_names = sorted(
            {
                str(run.get("workflowName") or run.get("name") or "").strip()
                for run in failing_runs
                if str(run.get("workflowName") or run.get("name") or "").strip()
            }
        )
        title = f"{len(failing_runs)} failing workflows on {branch}" + (
            f" ({', '.join(workflow_names)})" if workflow_names else ""
        )
    ctx = CiFixContext(
        owner=owner,
        repo=repo,
        number=None,
        title=title,
        url=url or f"https://github.com/{repo_full_name}/tree/{branch}",
        base_branch=branch,
        head_branch=branch,
        head_sha=str(primary.get("headSha") or "").strip(),
        skipped_check_names=tuple(dict.fromkeys(skipped_names)),
        failing_checks=tuple(checks),
        task="",
        target_kind=CI_TARGET_BRANCH,
        target_branch=branch,
    )
    return replace(ctx, task=_build_task(ctx))


def _failing_runs(value: Any) -> list[dict[str, Any]]:
    """Return every failing workflow run in the latest-commit group."""
    return [item for item in _list_value(value) if _is_failing_check(item)]


def _latest_branch_run_group(value: Any) -> list[dict[str, Any]]:
    runs = _list_value(value)
    if not runs:
        return []
    latest_head_sha = str(runs[0].get("headSha") or "").strip()
    if not latest_head_sha:
        return runs
    return [run for run in runs if str(run.get("headSha") or "").strip() == latest_head_sha]


def _checks_from_branch_run(
    repo_full_name: str,
    *,
    failing_run: dict[str, Any],
    run_id: str,
    run_url: str,
    workflow_name: str,
    github_token: str | None,
) -> tuple[tuple[FailingCheck, ...], tuple[str, ...]]:
    jobs: list[dict[str, Any]] = []
    if run_id:
        try:
            payload = run_gh_json(
                ["run", "view", run_id, "--json", "jobs"],
                repo=repo_full_name,
                github_token=github_token,
            )
            jobs = _list_value(payload.get("jobs"))
        except GitHubCiFixError:
            jobs = []

    failing_jobs = tuple(
        _failing_check_from_branch_job(
            repo_full_name,
            job,
            run_id=run_id,
            run_url=run_url,
            workflow_name=workflow_name,
            github_token=github_token,
        )
        for job in jobs
        if _is_failing_check(job)
    )
    skipped = tuple(_check_name(job) for job in jobs if _is_skipped(job))
    if failing_jobs:
        return failing_jobs, skipped

    log_excerpt = ""
    if run_id:
        log_excerpt = _fetch_log_excerpt(
            repo_full_name,
            run_id=run_id,
            job_id="",
            github_token=github_token,
        )
    return (
        (
            FailingCheck(
                name=workflow_name or str(failing_run.get("name") or "GitHub Actions run"),
                conclusion=str(failing_run.get("conclusion") or "").lower(),
                details_url=run_url,
                workflow_name=workflow_name,
                run_id=run_id,
                log_excerpt=log_excerpt,
            ),
        ),
        skipped,
    )


def _failing_check_from_branch_job(
    repo_full_name: str,
    job: dict[str, Any],
    *,
    run_id: str,
    run_url: str,
    workflow_name: str,
    github_token: str | None,
) -> FailingCheck:
    details_url = str(job.get("url") or run_url or "")
    job_id = str(job.get("databaseId") or job.get("id") or "")
    log_excerpt = ""
    if run_id:
        log_excerpt = _fetch_log_excerpt(
            repo_full_name,
            run_id=run_id,
            job_id=job_id,
            github_token=github_token,
        )
    return FailingCheck(
        name=str(job.get("name") or "unnamed job"),
        conclusion=str(job.get("conclusion") or job.get("status") or "").lower(),
        details_url=details_url,
        workflow_name=workflow_name,
        run_id=run_id,
        job_id=job_id,
        log_excerpt=log_excerpt,
    )


def _failing_check_from_rollup(
    repo_full_name: str,
    item: dict[str, Any],
    *,
    github_token: str | None,
) -> FailingCheck:
    details_url = str(item.get("detailsUrl") or item.get("targetUrl") or "")
    run_id, job_id = _actions_ids(details_url)
    log_excerpt = ""
    if run_id:
        log_excerpt = _fetch_log_excerpt(
            repo_full_name,
            run_id=run_id,
            job_id=job_id,
            github_token=github_token,
        )
    return FailingCheck(
        name=str(item.get("name") or item.get("context") or "unnamed check"),
        conclusion=str(item.get("conclusion") or item.get("state") or "").lower(),
        details_url=details_url,
        workflow_name=str(item.get("workflowName") or ""),
        run_id=run_id,
        job_id=job_id,
        log_excerpt=log_excerpt,
    )


def _fetch_log_excerpt(
    repo_full_name: str,
    *,
    run_id: str,
    job_id: str,
    github_token: str | None,
) -> str:
    args = ["run", "view", run_id, "--log"]
    if job_id:
        args.extend(["--job", job_id])
    try:
        raw = run_gh_text(args, repo=repo_full_name, github_token=github_token, timeout=180)
    except GitHubCiFixError as exc:
        return f"Log unavailable: {exc.message}"
    return _log_excerpt(raw)


def _log_excerpt(raw: str) -> str:
    lines = raw.splitlines()
    interesting: list[str] = []
    for index, line in enumerate(lines):
        lowered = line.lower()
        if any(
            marker in lowered
            for marker in ("::error", "error:", "failed", "failure", "traceback", "exception")
        ):
            start = max(0, index - 3)
            end = min(len(lines), index + 8)
            interesting.extend(lines[start:end])
            interesting.append("")
    if not interesting:
        interesting = lines[-80:]
    excerpt = "\n".join(interesting).strip()
    return excerpt[-_MAX_LOG_CHARS:]


def _build_task(ctx: CiFixContext) -> str:
    masker = MaskingRules(MaskingPolicy.from_env())
    if ctx.target_kind == CI_TARGET_BRANCH:
        branch = ctx.target_branch or ctx.base_branch
        lines = [
            f"Fix the failing GitHub Actions CI checks for {ctx.owner}/{ctx.repo} branch {branch}.",
            "",
            f"Branch: {branch}",
            f"Failing commit SHA: {ctx.head_sha}",
            f"Primary failing run: {ctx.url}",
            f"Run summary: {ctx.title}",
            "The workspace is a fresh OpenSRE repair branch based on the target branch.",
            "Repair every failing check listed below from all failing workflows on that commit.",
            "",
            "Failing checks and log excerpts:",
        ]
    else:
        lines = [
            f"Fix the failing GitHub Actions CI checks for {ctx.owner}/{ctx.repo} PR #{ctx.number}.",
            "",
            f"PR: {ctx.url}",
            f"Title: {ctx.title}",
            f"Base branch: {ctx.base_branch}",
            f"Head branch to edit and push: {ctx.head_branch}",
            f"Head SHA: {ctx.head_sha}",
            "",
            "Failing checks and log excerpts:",
        ]
    log_budget = _MAX_TASK_LOG_CHARS
    for check in ctx.failing_checks:
        lines.extend(
            [
                "",
                f"- Check: {check.name}",
                f"  Workflow: {check.workflow_name}",
                f"  Conclusion: {check.conclusion}",
                f"  Details: {check.details_url}",
            ]
        )
        if check.log_excerpt and log_budget > 0:
            excerpt = check.log_excerpt[:log_budget]
            log_budget -= len(excerpt)
            lines.extend(["  Log excerpt:", _indent(excerpt, prefix="    ")])
    lines.extend(
        [
            "",
            "Make the smallest repository change that addresses the observed CI failure.",
            "Do not silence CI, skip tests, or weaken checks unless the log proves the check itself is wrong.",
            "Preserve unrelated user changes. Run the smallest relevant local verification command when practical.",
            "Finish with a concise summary of files changed and verification performed.",
        ]
    )
    return "\n".join(masker.mask(line) for line in lines)


def _actions_ids(details_url: str) -> tuple[str, str]:
    match = _ACTIONS_URL_RE.search(details_url)
    if match is None:
        return "", ""
    return match.group("run_id"), match.group("job_id") or ""


def _head_repo_full_name(pr: dict[str, Any]) -> str:
    head_repo = pr.get("headRepository")
    if isinstance(head_repo, dict):
        name_with_owner = str(head_repo.get("nameWithOwner") or "").strip()
        if name_with_owner:
            return name_with_owner
        name = str(head_repo.get("name") or "").strip()
    else:
        name = ""
    owner = pr.get("headRepositoryOwner")
    owner_login = str(owner.get("login") or "").strip() if isinstance(owner, dict) else ""
    return f"{owner_login}/{name}" if owner_login and name else ""


def _is_failing_check(item: dict[str, Any]) -> bool:
    conclusion = str(item.get("conclusion") or "").strip().upper()
    state = str(item.get("state") or "").strip().upper()
    return conclusion in _FAILED_CONCLUSIONS or state in _FAILED_STATES


def _check_name(item: dict[str, Any]) -> str:
    return str(item.get("name") or item.get("context") or "unnamed check")


def _is_skipped(item: dict[str, Any]) -> bool:
    return str(item.get("conclusion") or "").strip().upper() == "SKIPPED"


def _list_value(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _int_value(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _normalize_branch(branch: str | None) -> str:
    cleaned = str(branch or "").strip()
    for prefix in ("refs/remotes/origin/", "refs/heads/", "origin/"):
        if cleaned.startswith(prefix):
            return cleaned.removeprefix(prefix).strip()
    return cleaned


def _indent(value: str, *, prefix: str) -> str:
    return "\n".join(f"{prefix}{line}" if line else "" for line in value.splitlines())


__all__ = [
    "CI_TARGET_BRANCH",
    "CI_TARGET_PR",
    "CiFixContext",
    "FailingCheck",
    "PullRequestRef",
    "gather_ci_fix_context",
    "parse_pr_url",
]
