"""Agent-neutral coding-agent seam.

Callers depend on this interface — a :class:`CodingResult`, a
:func:`run_coding_task` entry point, and :func:`verify_coding_agent` readiness —
rather than on a specific agent. Pi is the only wired backend today; others plug in
behind the same interface (see :mod:`integrations.coding_agent.runner`).
"""

from __future__ import annotations

from integrations.coding_agent.config import (
    coding_agent_provider,
    coding_model,
    coding_timeout_seconds,
    coding_workspace,
)
from integrations.coding_agent.models import CodingResult
from integrations.coding_agent.runner import run_coding_task, verify_coding_agent

__all__ = [
    "CodingResult",
    "coding_agent_provider",
    "coding_model",
    "coding_timeout_seconds",
    "coding_workspace",
    "run_coding_task",
    "verify_coding_agent",
]
