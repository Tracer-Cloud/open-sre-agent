"""Pi coding-agent backend — the one place the seam is coupled to Pi.

Adapts ``integrations/pi`` to the neutral :class:`CodingResult`. Adding another
backend (codex/claude_code/…) later means adding a sibling module and registering
it in :mod:`integrations.coding_agent.runner`, with no change to callers.
"""

from __future__ import annotations

from integrations.coding_agent.models import CodingResult
from integrations.pi import run_pi_coding_task, verify_pi_coding


def run(task: str, *, workspace: str, model: str | None, timeout_sec: float) -> CodingResult:
    result = run_pi_coding_task(task, workspace=workspace, model=model, timeout_sec=timeout_sec)
    return CodingResult(
        success=result.success,
        summary=result.summary,
        changed_files=result.changed_files,
        diff=result.diff,
        diff_truncated=result.diff_truncated,
        returncode=result.returncode,
        timed_out=result.timed_out,
        error=result.error,
    )


def verify() -> tuple[bool, str]:
    return verify_pi_coding()
