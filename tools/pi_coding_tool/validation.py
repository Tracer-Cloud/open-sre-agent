"""Input validation and request resolution for the Pi coding tool.

Turns the loose tool arguments (``task`` / ``workspace`` / ``model``) into a
validated, fully-resolved :class:`ResolvedRequest`, applying config defaults
(``PI_CODING_WORKSPACE`` / ``PI_CODING_MODEL`` / ``PI_CODING_TIMEOUT_SECONDS``).
Each validator raises :class:`PiCodingError` with ``kind="invalid_input"`` on a
bad value.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from integrations.pi import pi_coding_model, pi_coding_timeout_seconds, pi_coding_workspace
from tools.pi_coding_tool.errors import ERR_INVALID_INPUT, PiCodingError

_MAX_TASK_CHARS = 4000


@dataclass(frozen=True)
class ResolvedRequest:
    """A validated, fully-resolved coding request ready to execute."""

    task: str
    workspace: str
    model: str | None
    timeout_sec: float


def validate_task(task: str | None) -> str:
    cleaned = (task or "").strip()
    if not cleaned:
        raise PiCodingError(ERR_INVALID_INPUT, "task is required and must be non-empty.")
    if len(cleaned) > _MAX_TASK_CHARS:
        raise PiCodingError(
            ERR_INVALID_INPUT,
            f"task is too long ({len(cleaned)} chars); keep it under {_MAX_TASK_CHARS}.",
        )
    return cleaned


def validate_workspace(workspace: str | None) -> str:
    resolved = (workspace or "").strip() or pi_coding_workspace()
    path = Path(resolved).expanduser()
    if not path.exists():
        raise PiCodingError(ERR_INVALID_INPUT, f"workspace does not exist: {path}")
    if not path.is_dir():
        raise PiCodingError(ERR_INVALID_INPUT, f"workspace is not a directory: {path}")
    return str(path)


def validate_model(model: str | None) -> str | None:
    resolved = (model or "").strip() or pi_coding_model()
    if resolved is None:
        return None
    # Pi accepts "provider/model" and shorthands (e.g. "sonnet:high"); only reject
    # obviously malformed values (whitespace inside the token).
    if any(ch.isspace() for ch in resolved):
        raise PiCodingError(
            ERR_INVALID_INPUT,
            f"model must not contain whitespace; got {resolved!r}.",
        )
    return resolved


def resolve_request(task: str | None, workspace: str | None, model: str | None) -> ResolvedRequest:
    """Validate + normalize the tool arguments into a :class:`ResolvedRequest`."""
    return ResolvedRequest(
        task=validate_task(task),
        workspace=validate_workspace(workspace),
        model=validate_model(model),
        timeout_sec=pi_coding_timeout_seconds(),
    )
