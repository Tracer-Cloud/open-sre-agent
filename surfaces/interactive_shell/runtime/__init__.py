"""Interactive-shell runtime package.

Re-exports session / scheduling names used by the shell.
"""

from __future__ import annotations

from infrastructure.scheduling.task_registry import TaskRegistry
from infrastructure.scheduling.task_types import TaskKind, TaskRecord, TaskStatus
from surfaces.interactive_shell.runtime.context import (
    ReplRuntimeContext,
    SessionBootstrapSpec,
    create_repl_runtime_context,
    prepare_repl_session,
)
from surfaces.interactive_shell.session.background_investigations import (
    BackgroundInvestigationRecord,
    BackgroundNotificationPreferences,
)
from surfaces.interactive_shell.session.session import Session

__all__ = [
    "BackgroundInvestigationRecord",
    "BackgroundNotificationPreferences",
    "ReplRuntimeContext",
    "Session",
    "SessionBootstrapSpec",
    "TaskKind",
    "TaskRecord",
    "TaskRegistry",
    "TaskStatus",
    "create_repl_runtime_context",
    "prepare_repl_session",
]
