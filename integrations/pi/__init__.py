"""Pi coding integration: config + client + verifier for running Pi as a coding agent.

This is the *coding-task* side of Pi (the hands). The LLM-provider side (the brain)
lives in ``integrations/llm_cli/pi_cli.py``. Both share the same ``pi`` binary and
credentials; this package owns the config and the agentic-run client used by the
``tools/pi_coding_tool`` tool.

Env vars
--------
PI_CODING_ENABLED          Opt-in flag. The tool is unavailable unless this is set
                           to a truthy value (1/true/yes/on). Off by default
                           because the tool mutates the working tree.
PI_CODING_MODEL            Optional Pi model override (provider/model form).
PI_CODING_TIMEOUT_SECONDS  Optional per-task timeout (default 600, clamped 60-1800).
PI_CODING_WORKSPACE        Optional default workspace path (default: cwd).
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from integrations.llm_cli.timeout_utils import resolve_timeout_from_env
from integrations.pi.client import PiCodingResult, run_pi_coding_task
from integrations.pi.verifier import verify_pi_coding

_DEFAULT_TIMEOUT_SEC = 600.0
_MIN_TIMEOUT_SEC = 60.0
_MAX_TIMEOUT_SEC = 1800.0
_TRUTHY = {"1", "true", "yes", "on"}


def is_pi_coding_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether the Pi coding tool is opted in via ``PI_CODING_ENABLED``."""
    source = env if env is not None else os.environ
    return source.get("PI_CODING_ENABLED", "").strip().lower() in _TRUTHY


def pi_coding_model(env: Mapping[str, str] | None = None) -> str | None:
    """Configured Pi model override, or ``None`` to use Pi's default."""
    source = env if env is not None else os.environ
    return source.get("PI_CODING_MODEL", "").strip() or None


def pi_coding_timeout_seconds() -> float:
    """Per-task timeout from ``PI_CODING_TIMEOUT_SECONDS`` (clamped)."""
    return resolve_timeout_from_env(
        env_key="PI_CODING_TIMEOUT_SECONDS",
        default=_DEFAULT_TIMEOUT_SEC,
        minimum=_MIN_TIMEOUT_SEC,
        maximum=_MAX_TIMEOUT_SEC,
    )


def pi_coding_workspace(env: Mapping[str, str] | None = None) -> str:
    """Default workspace path (``PI_CODING_WORKSPACE`` or the current directory)."""
    source = env if env is not None else os.environ
    return source.get("PI_CODING_WORKSPACE", "").strip() or os.getcwd()


__all__ = [
    "PiCodingResult",
    "is_pi_coding_enabled",
    "pi_coding_model",
    "pi_coding_timeout_seconds",
    "pi_coding_workspace",
    "run_pi_coding_task",
    "verify_pi_coding",
]
