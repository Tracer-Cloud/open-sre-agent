"""Runtime feature flags for interactive-shell action orchestration.

These flags gate optional behavior at the planner/orchestration boundary. Keep
them dependency-light: this module must stay importable without pulling in the
LLM, tool runners, or the investigation pipeline.
"""

from __future__ import annotations

# Temporary kill-switch for the natural-language investigation loop in the
# interactive shell. When ``False`` the planner is NOT offered the
# ``investigation_start`` tool, so diagnostic / incident-style prompts can no
# longer trigger the heavyweight RCA pipeline from the REPL -- they fall through
# to the conversational assistant instead.
#
# Scope: this gates ONLY the planner's natural-language path. The
# ``/sample-alert`` command and the local alert listener still run
# investigations; they do not go through ``investigation_start``.
#
# Flip to ``True`` to restore natural-language investigation routing (which also
# re-enables the investigation routing scenarios that skip while this is False).
INTERACTIVE_SHELL_INVESTIGATION_ENABLED = False


def investigation_loop_enabled() -> bool:
    """Return whether the planner may select the natural-language investigation tool.

    Reads the module-level flag at call time so tests can monkeypatch it.
    """
    return INTERACTIVE_SHELL_INVESTIGATION_ENABLED


__all__ = [
    "INTERACTIVE_SHELL_INVESTIGATION_ENABLED",
    "investigation_loop_enabled",
]
