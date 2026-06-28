"""Core runtime engine for the interactive shell.

Session state and context now live in ``interactive_shell.session``; this
package owns the remaining runtime engine concerns (task registry, mutable
runtime state, prompt manager, token accounting, turn detection).
"""

from __future__ import annotations

from interactive_shell.runtime.core.tasks import TaskRegistry

__all__ = ["TaskRegistry"]
