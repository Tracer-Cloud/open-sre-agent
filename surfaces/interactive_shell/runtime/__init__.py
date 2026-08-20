"""Interactive-shell runtime package.

Re-exports session / scheduling names used by the shell. Bootstraps the project
``platform`` package before importing ``platform.scheduling`` so embedding hosts
that already cached stdlib ``platform`` still load cleanly.
"""

from __future__ import annotations

from config.platform_bootstrap import ensure_project_platform_package

ensure_project_platform_package()

from platform.scheduling.task_registry import TaskRegistry  # noqa: E402
from platform.scheduling.task_types import TaskKind, TaskRecord, TaskStatus  # noqa: E402
from surfaces.interactive_shell.runtime.context import (  # noqa: E402
    ReplRuntimeContext,
    SessionBootstrapSpec,
    create_repl_runtime_context,
    prepare_repl_session,
)
from surfaces.interactive_shell.session.background_investigations import (  # noqa: E402
    BackgroundInvestigationRecord,
    BackgroundNotificationPreferences,
)
from surfaces.interactive_shell.session.session import Session  # noqa: E402

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
