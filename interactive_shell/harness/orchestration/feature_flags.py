"""Runtime feature flags for interactive-shell action orchestration.

These flags gate optional behavior at the planner/orchestration boundary. Keep
them dependency-light: this module must stay importable without pulling in the
LLM, tool runners, or the investigation pipeline.
"""

from __future__ import annotations

# Natural-language investigation dispatch in the interactive shell. When ``True``
# the planner is offered ``investigation_start``, so incident-style prompts can
# trigger the RCA pipeline from the REPL. When ``False`` (emergency rollback
# only) those prompts fall through to the conversational assistant instead.
#
# Scope: this gates ONLY the planner's natural-language path. The
# ``/sample-alert`` command and the local alert listener still run
# investigations; they do not go through ``investigation_start``.
#
# Keep ``True`` as the shipped default. Set ``False`` only for emergency rollback;
# investigation dispatch scenarios then skip in ``turn-live`` (they do not fail
# ``turn-checks``).
INTERACTIVE_SHELL_INVESTIGATION_ENABLED = True


def investigation_loop_enabled() -> bool:
    """Return whether the planner may select the natural-language investigation tool.

    Reads the module-level flag at call time so tests can monkeypatch it.
    """
    return INTERACTIVE_SHELL_INVESTIGATION_ENABLED


__all__ = [
    "INTERACTIVE_SHELL_INVESTIGATION_ENABLED",
    "investigation_loop_enabled",
]
