"""Provider-agnostic entry point for running a coding agent over a workspace.

Resolves the configured backend (``CODING_AGENT``, default ``pi``) and dispatches
to it. Today only ``pi`` is wired; new backends register in ``_BACKENDS`` and every
caller keeps using :func:`run_coding_task` / :func:`verify_coding_agent` unchanged.
"""

from __future__ import annotations

from collections.abc import Callable

from integrations.coding_agent.config import coding_agent_provider
from integrations.coding_agent.models import CodingResult
from integrations.coding_agent.pi_backend import run as _pi_run
from integrations.coding_agent.pi_backend import verify as _pi_verify

_RunFn = Callable[..., CodingResult]
_VerifyFn = Callable[[], tuple[bool, str]]

# provider name -> (run, verify)
_BACKENDS: dict[str, tuple[_RunFn, _VerifyFn]] = {
    "pi": (_pi_run, _pi_verify),
}


def _resolve(provider: str | None) -> tuple[str, tuple[_RunFn, _VerifyFn] | None]:
    name = (provider or coding_agent_provider()).strip().lower()
    return name, _BACKENDS.get(name)


def verify_coding_agent(provider: str | None = None) -> tuple[bool, str]:
    """Whether the configured coding agent is installed/ready (never raises)."""
    name, backend = _resolve(provider)
    if backend is None:
        supported = ", ".join(sorted(_BACKENDS))
        return False, f"Unsupported coding agent '{name}'. Set CODING_AGENT to one of: {supported}."
    _run, verify = backend
    return verify()


def run_coding_task(
    task: str,
    *,
    workspace: str,
    model: str | None,
    timeout_sec: float,
    provider: str | None = None,
) -> CodingResult:
    """Run the configured coding agent on *task* in *workspace*."""
    name, backend = _resolve(provider)
    if backend is None:
        return CodingResult(success=False, summary="", error=f"Unsupported coding agent '{name}'.")
    run, _verify = backend
    return run(task, workspace=workspace, model=model, timeout_sec=timeout_sec)
