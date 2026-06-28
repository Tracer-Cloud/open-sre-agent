from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from interactive_shell.runtime.background.models import (
    BackgroundInvestigationRecord,
    BackgroundNotificationPreferences,
)
from interactive_shell.runtime.core.tasks import TaskRegistry
from platform.common.task_types import TaskKind, TaskRecord, TaskStatus

if TYPE_CHECKING:
    from interactive_shell.session import (
        ReplRuntimeContext,
        ReplSession,
        ReplSessionBootstrapSpec,
        create_repl_runtime_context,
        prepare_repl_session,
    )

# Session state/context live in ``interactive_shell.session`` (the canonical
# home). They are re-exported here lazily so the ergonomic
# ``from interactive_shell.runtime import ReplSession`` keeps working without an
# import cycle (session.context imports runtime.core.state at module load).
_SESSION_EXPORTS = frozenset(
    {
        "ReplRuntimeContext",
        "ReplSession",
        "ReplSessionBootstrapSpec",
        "create_repl_runtime_context",
        "prepare_repl_session",
    }
)


def __getattr__(name: str) -> Any:
    if name in _SESSION_EXPORTS:
        _session = importlib.import_module("interactive_shell.session")
        return getattr(_session, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BackgroundInvestigationRecord",
    "BackgroundNotificationPreferences",
    "ReplRuntimeContext",
    "ReplSession",
    "ReplSessionBootstrapSpec",
    "TaskKind",
    "TaskRecord",
    "TaskRegistry",
    "TaskStatus",
    "create_repl_runtime_context",
    "prepare_repl_session",
]
