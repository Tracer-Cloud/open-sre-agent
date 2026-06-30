"""Lifecycle and execution orchestration for the Pi coding tool.

These are the stages ``PiCodingTool.run`` drives, kept as small free functions so
the tool class stays a thin agent-facing contract:

- :func:`ensure_enabled`    — opt-in gate (``PI_CODING_ENABLED``).
- :func:`ensure_cli_ready`  — Pi binary installed and authenticated.
- :func:`execute`           — run the polled Pi process (``integrations/pi`` client).
- :func:`to_output`         — shape a stable result dict (with ``error_kind``).
- :func:`error_output`      — the same dict shape for an early/expected failure.
"""

from __future__ import annotations

from typing import Any, Final

from integrations.pi import (
    PiCodingResult,
    is_pi_coding_enabled,
    run_pi_coding_task,
    verify_pi_coding,
)
from tools.pi_coding_tool.errors import (
    ERR_CLI_UNAVAILABLE,
    ERR_DISABLED,
    ERR_EXECUTION,
    ERR_TIMEOUT,
    PiCodingError,
)
from tools.pi_coding_tool.validation import ResolvedRequest

#: Evidence source tag stamped on every result dict.
SOURCE: Final = "knowledge"

_DISABLED_MESSAGE = (
    "Pi coding tool is disabled. Set PI_CODING_ENABLED=1 (and install/authenticate "
    "the Pi CLI) to enable it."
)


def ensure_enabled() -> None:
    if not is_pi_coding_enabled():
        raise PiCodingError(ERR_DISABLED, _DISABLED_MESSAGE)


def ensure_cli_ready() -> None:
    available, detail = verify_pi_coding()
    if not available:
        raise PiCodingError(ERR_CLI_UNAVAILABLE, f"Pi CLI is not ready: {detail}")


def execute(request: ResolvedRequest) -> PiCodingResult:
    return run_pi_coding_task(
        request.task,
        workspace=request.workspace,
        model=request.model,
        timeout_sec=request.timeout_sec,
    )


def to_output(result: PiCodingResult) -> dict[str, Any]:
    error_kind: str | None = None
    if not result.success:
        error_kind = ERR_TIMEOUT if result.timed_out else ERR_EXECUTION
    return {
        "source": SOURCE,
        "success": result.success,
        "error_kind": error_kind,
        "summary": result.summary,
        "changed_files": result.changed_files,
        "diff": result.diff,
        "diff_truncated": result.diff_truncated,
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "error": result.error,
    }


def error_output(kind: str, message: str) -> dict[str, Any]:
    return {"source": SOURCE, "success": False, "error_kind": kind, "error": message}
