"""Pi coding-task client.

Runs the Pi CLI (https://pi.dev) in headless agentic mode inside a target
workspace so it can implement a coding task (read/write/edit/bash), then captures
what changed via git. This is the *hands* role for Pi, the inverse of the
``integrations/llm_cli`` provider role (the *brain*).

Execution model: Pi runs as a child process that is **polled to a deadline**
(``integrations.llm_cli.agent_exec.poll_agent_process``) rather than a single
blocking call, so a long task is bounded and the process is terminated
gracefully (SIGTERM, then SIGKILL) on timeout.

Safety model (see issue: "Add Pi as an integration and tool for submitting
coding tasks"): the task prompt forbids commits/pushes and destructive git
commands, and the caller gates invocation (the ``tools`` layer only runs this when
``PI_CODING_ENABLED`` is set — off by default, since the tool is offered on the
investigation surface). This module only edits the working tree and reports the
diff; it never commits, pushes, or opens a PR.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from integrations.git import GitCommandError, capture_worktree_changes, is_git_repo
from integrations.llm_cli.agent_exec import (
    build_guarded_task_prompt,
    classify_agent_outcome,
    poll_agent_process,
)
from integrations.llm_cli.binary_resolver import (
    candidate_binary_names,
    default_cli_fallback_paths,
    resolve_cli_binary,
)
from integrations.llm_cli.env_overrides import PI_PROVIDER_ENV_KEYS, nonempty_env_values
from integrations.llm_cli.subprocess_env import build_cli_subprocess_env

_MAX_DIFF_CHARS = 20000
_INSTALL_HINT = "npm i -g @earendil-works/pi-coding-agent"


@dataclass(frozen=True)
class PiCodingResult:
    """Outcome of a Pi coding task run."""

    success: bool
    summary: str
    changed_files: list[str] = field(default_factory=list)
    diff: str = ""
    returncode: int = 0
    timed_out: bool = False
    error: str | None = None
    diff_truncated: bool = False


def _resolve_pi_binary() -> str | None:
    return resolve_cli_binary(
        explicit_env_key="PI_BIN",
        binary_names=candidate_binary_names("pi"),
        fallback_paths=lambda: default_cli_fallback_paths("pi"),
    )


def _pi_subprocess_env() -> dict[str, str]:
    """Color-free env with BYOK provider keys forwarded to the Pi subprocess."""
    env: dict[str, str] = {"NO_COLOR": "1"}
    env.update(nonempty_env_values(PI_PROVIDER_ENV_KEYS))
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
        val = os.environ.get(key, "").strip()
        if val:
            env[key] = val
    return env


def run_pi_coding_task(
    task: str,
    *,
    workspace: str,
    model: str | None,
    timeout_sec: float,
) -> PiCodingResult:
    """Run Pi against *task* in *workspace*; return summary + diff of what changed.

    Pre-flight failures (missing binary, bad workspace) and execution failures
    (timeout, provider limit, no-op) are all returned as a populated
    ``PiCodingResult`` with ``success=False`` and a human-readable ``error`` — this
    function does not raise for expected conditions.
    """
    binary = _resolve_pi_binary()
    if not binary:
        return PiCodingResult(
            success=False,
            summary="",
            returncode=-1,
            error=f"Pi CLI not found on PATH or known locations. Install with: {_INSTALL_HINT} or set PI_BIN.",
        )

    ws = str(Path(workspace).expanduser()) if workspace else os.getcwd()
    if not Path(ws).is_dir():
        return PiCodingResult(
            success=False, summary="", returncode=-1, error=f"workspace is not a directory: {ws}"
        )

    # The tool's contract is "edit + return a reviewable diff". Without git we can
    # neither capture nor review changes, so fail fast *before* editing rather than
    # letting Pi edit files and reporting a misleading success with an empty diff.
    try:
        repo_ok = is_git_repo(ws)
    except GitCommandError as exc:
        return PiCodingResult(success=False, summary="", returncode=-1, error=exc.message)
    if not repo_ok:
        return PiCodingResult(
            success=False,
            summary="",
            returncode=-1,
            error=f"workspace is not a git repository; the tool needs git to capture changes: {ws}",
        )

    prompt = build_guarded_task_prompt(task, agent_label="the Pi coding agent")
    argv: list[str] = [binary, "-p", prompt]
    resolved_model = (model or "").strip()
    if resolved_model:
        argv.extend(["--model", resolved_model])

    outcome = poll_agent_process(
        argv,
        cwd=ws,
        env=build_cli_subprocess_env(_pi_subprocess_env()),
        timeout_sec=timeout_sec,
    )
    if outcome.spawn_error:
        return PiCodingResult(success=False, summary="", returncode=-1, error=outcome.spawn_error)

    changes = capture_worktree_changes(ws, max_diff_chars=_MAX_DIFF_CHARS)
    classified = classify_agent_outcome(
        outcome,
        agent_name="pi",
        changed_files=changes.changed_files,
        timeout_sec=timeout_sec,
    )
    return PiCodingResult(
        success=classified.success,
        summary=classified.summary,
        changed_files=changes.changed_files,
        diff=changes.diff,
        returncode=outcome.returncode,
        timed_out=outcome.timed_out,
        error=classified.error,
        diff_truncated=changes.diff_truncated,
    )
